# TODO: actually handle errors...

import rumps, requests, enum, webbrowser, datetime, dateutil, dateutil.parser, inflect, tinydb, os, logging

rumps.debug_mode(True)

red_icon = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets/red.png"))
green_icon = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets/green.png"))
db_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "db.json"))

p = inflect.engine()
db = tinydb.TinyDB(db_file)

ignore_build_ids = db.table("ignore_build_ids")
notified_build_ids = db.table("notified_build_ids")

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s [%(levelname)s]: %(message)s")
log = logging.getLogger("cfstatus")


class Settings(tinydb.table.Table):
    @property
    def codefresh_api_key(self) -> str:
        return self.find_by_key("codefresh_api_key")

    @property
    def github_username(self) -> str:
        return self.find_by_key("github_username")

    @property
    def status_check_interval(self) -> str:
        return self.find_by_key("status_check_interval")

    @property
    def notifications_enabled(self) -> bool:
        return self.find_by_key("notifications_enabled")

    @property
    def show_build_on_restart(self) -> bool:
        return self.find_by_key("show_build_on_restart")

    def __init__(self):
        super().__init__(db.storage, "settings")

    def find_by_key(self, key: str):
        all_settings = self.all()
        return next(iter([s for s in all_settings if not s.get(key) is None]), {}).get(key, None)

    def set_default_value(self, key, value, overwrite=False):
        exists = self.find_by_key(key)
        if exists is not None and not overwrite:
            return
        mapping = {key: value}
        self.upsert(mapping, None)


settings = Settings()


class CallableEnum(enum.Enum):
    def __call__(self):
        return self.value


class TimePeriods(CallableEnum):
    today = "Today"
    this_week = "This Week"
    this_month = "This Month"


class SettingsChoices(CallableEnum):
    codefresh_api_key = "Codefresh API Key"
    github_username = "Github Username"
    status_check_interval = "Status Check Interval"
    notifications = "Notifications"
    show_build_on_restart = "Show Build on Restart"


class ErroredBuildsMenuChoices(CallableEnum):
    view = "View"
    restart = "Restart"
    mark_fixed = "Mark Fixed"


class MenuState(CallableEnum):
    off = 0
    on = 1
    mixed = -1


class Menus(CallableEnum):
    time_period = "Time period"
    errored_builds = "Errored Builds"
    settings = "Settings"


time_period_to_filter_value = {
    TimePeriods.today(): "day",
    TimePeriods.this_week(): "week",
    TimePeriods.this_month(): "month",
}

DEFAULT_TIME_PERIOD = time_period_to_filter_value[TimePeriods.today()]


class TimePeriodMenu(rumps.MenuItem):
    selected_time_period: str = "day"

    def __init__(self, app):
        super().__init__(Menus.time_period())
        self.app = app
        self[TimePeriods.today()] = rumps.MenuItem(TimePeriods.today(), callback=self._set_time_period)
        # HACK: would love a cleaner way to do this default menu setting
        self[TimePeriods.today()].state = MenuState.on()
        self[TimePeriods.this_week()] = rumps.MenuItem(TimePeriods.this_week(), callback=self._set_time_period)
        self[TimePeriods.this_month()] = rumps.MenuItem(TimePeriods.this_month(), callback=self._set_time_period)

    def _set_time_period(self, sender):
        self.selected_time_period = time_period_to_filter_value.get(sender.title, DEFAULT_TIME_PERIOD)
        for item in self.values():
            item.state = MenuState.off()
        sender.state = MenuState.on()
        self.app._get_cf_builds(None)


class ErroredBuildsMenu(rumps.MenuItem):
    def __init__(self):
        super().__init__(Menus.errored_builds())


class ErroredBuildsMenuItem(rumps.MenuItem):
    def __init__(self, name, build, app):
        super().__init__(name)
        self.build = build
        self.app = app
        self[ErroredBuildsMenuChoices.view()] = rumps.MenuItem(
            ErroredBuildsMenuChoices.view(), callback=lambda sender: self._view_build(self.build["id"])
        )
        # TODO:(idea): if we restart a build, it would be cool to check if it succeeded
        # when we look for builds, and if so, auto-mark it fixed.
        self[ErroredBuildsMenuChoices.restart()] = rumps.MenuItem(
            ErroredBuildsMenuChoices.restart(), callback=self._restart_failed_build
        )
        self[ErroredBuildsMenuChoices.mark_fixed()] = rumps.MenuItem(
            ErroredBuildsMenuChoices.mark_fixed(), callback=self._mark_fixed
        )

    def _view_build(self, build_id):
        webbrowser.open(f"https://g.codefresh.io/build/{build_id}")

    def _mark_fixed(self, sender):
        log.info("ignoring build with id " + self.build["id"])
        ignore_build_ids.insert({"build_id": self.build["id"]})
        del self.app.menu[Menus.errored_builds()][self.build["id"]]
        self.app.last_errored_builds.remove(self.build)
        self.app._update_errored_builds_menu()

    def _restart_failed_build(self, sender):
        log.info("restarting build with ID: " + self.build["id"])
        response = requests.get(
            f"https://g.codefresh.io/api/builds/rebuild/{self.build['id']}",
            headers={"Authorization": settings.codefresh_api_key},
        )
        response.raise_for_status()
        new_build_id = response.json()
        log.info("created build " + new_build_id)
        if not settings.show_build_on_restart:
            log.info("show_build_on_restart disabled")
            return
        self._view_build(new_build_id)


class SettingsMenu(rumps.MenuItem):
    def __init__(self, app):
        super().__init__(Menus.settings())
        self.app = app
        self._set_defaults()
        self[SettingsChoices.notifications()] = rumps.MenuItem(
            SettingsChoices.notifications(), callback=self._toggle_setting("notifications_enabled")
        )
        self[SettingsChoices.show_build_on_restart()] = rumps.MenuItem(
            SettingsChoices.show_build_on_restart(), callback=self._toggle_setting("show_build_on_restart")
        )
        # TODO: need to clean up this default state setting
        self[SettingsChoices.notifications()].state = (
            MenuState.on() if settings.notifications_enabled else MenuState.off()
        )
        self[SettingsChoices.show_build_on_restart()].state = (
            MenuState.on() if settings.show_build_on_restart else MenuState.off()
        )
        self[rumps.separator] = rumps.separator
        self[SettingsChoices.codefresh_api_key()] = rumps.MenuItem(
            SettingsChoices.codefresh_api_key(),
            callback=self._gather_user_input("The Codefresh API key to authenticate with.", "codefresh_api_key",),
        )
        self[SettingsChoices.github_username()] = rumps.MenuItem(
            SettingsChoices.github_username(),
            callback=self._gather_user_input("The Github username used to filter builds.", "github_username",),
        )

        def _set_interval(interval):
            self.app.event_loop.interval = float(interval)

        self[SettingsChoices.status_check_interval()] = rumps.MenuItem(
            SettingsChoices.status_check_interval(),
            callback=self._gather_user_input(
                "The number of seconds between checking Codefresh builds.", "status_check_interval", _set_interval,
            ),
        )

    def _set_defaults(self):
        settings.set_default_value("status_check_interval", 10)
        settings.set_default_value("notifications_enabled", True)
        settings.set_default_value("show_build_on_restart", True)

    def _toggle_setting(self, db_key: str):
        def _toggle(sender):
            # TODO: could use some type checking around this eventually
            val = not settings.find_by_key(db_key)
            log.info(f"toggling settings[{db_key}] to {val}")
            settings.update({db_key: val})
            sender.state = MenuState.on() if val else MenuState.off()

        return _toggle

    def _gather_user_input(self, message, db_key, on_update=None):
        def _gather(sender):
            response = rumps.Window(
                title=sender.title,
                message=message,
                default_text=settings.find_by_key(db_key) or "",
                cancel=True,
                dimensions=(320, 60),
            ).run()
            if not response.clicked:
                return
            log.info(f"updating settings[{db_key}] with {response.text}")
            settings.update({db_key: response.text})
            if on_update:
                on_update(response.text)

        return _gather


class CodefreshStatusApp(rumps.App):
    last_errored_builds = []

    def __init__(self):
        super().__init__(
            "Codefresh Status",
            icon=green_icon,
            menu=[
                rumps.separator,
                ErroredBuildsMenu(),
                rumps.separator,
                TimePeriodMenu(self),
                SettingsMenu(self),
                rumps.separator,
            ],
        )
        self.event_loop = rumps.Timer(self._get_cf_builds, float(settings.status_check_interval))
        self.event_loop.start()

    # TODO: this runs in a separate thread, might need to add thread safety logic
    def _get_cf_builds(self, sender):
        if not settings.codefresh_api_key or not settings.github_username:
            log.error("missing required setting(s): codefresh_api_key, github_username")
            self.icon = red_icon
            self.title = "!! Missing Required Settings !!"
            return
        self.title = None
        log.info("getting cf builds")
        response = requests.get(
            "https://g.codefresh.io/api/workflow",
            headers={"Authorization": settings.codefresh_api_key},
            params={
                "inlineView[filters][0][selectedValue]": "type",
                "inlineView[filters][0][findType]": "is",
                "inlineView[filters][0][values][0]": "webhook",
                "inlineView[filters][0][values][1]": "build",
                "inlineView[filters][1][selectedValue]": "committer",
                "inlineView[filters][1][findType]": "is",
                "inlineView[filters][1][values][0]": settings.github_username,
                "inlineView[type]": "build",
                "inlineView[timeFrameStart][0]": self.menu[Menus.time_period()].selected_time_period,
                "limit": 99999999,
            },
        )
        log.info("got cf builds")
        response.raise_for_status()
        body = response.json()
        self.last_errored_builds = self._builds_with_errors(body)
        self._notify_failed_builds()
        self._update_errored_builds_menu()

    def _notify_failed_builds(self):
        log.info("processing notifications")
        # TODO: togglable notifiactions setting
        if not settings.notifications_enabled:
            log.info("notifications currently disabled")
            return
        if not self.last_errored_builds:
            return
        unseen_failed_builds = [
            b for b in self.last_errored_builds if not notified_build_ids.search(tinydb.where("build_id") == b["id"])
        ]
        if not unseen_failed_builds:
            log.info("no unseen failed builds to notify on")
            return
        log.info(f"{len(unseen_failed_builds)} unseen failed build(s), triggering notification")
        rumps.notification(
            title=f"{len(unseen_failed_builds)} failed {p.plural('build', len(unseen_failed_builds))} since last check",
            subtitle=", ".join(set([b["repoName"] for b in unseen_failed_builds])),
            message=None,
            sound=True,
        )
        ids = [{"build_id": b["id"]} for b in unseen_failed_builds]
        notified_build_ids.insert_multiple(ids)

    def _builds_with_errors(self, body) -> bool:
        builds = body["workflows"]["docs"]
        log.info(f"retrieved {len(builds)} total builds")
        all_errored_builds = [b for b in builds if b["status"] == "error" and b["id"]]
        log.info(f"found {len(all_errored_builds)} builds with errors")
        filtered_errored_builds = [
            b for b in all_errored_builds if not ignore_build_ids.contains(tinydb.where("build_id") == b["id"])
        ]
        log.info(f"filtered {len(all_errored_builds) - len(filtered_errored_builds)} builds with errors")
        return filtered_errored_builds

    def _update_errored_builds_menu(self):
        log.info("building errored builds menu")
        errored_builds_menu = self.menu[Menus.errored_builds()]
        len(errored_builds_menu) and errored_builds_menu.clear()
        if not self.last_errored_builds:
            self.icon = green_icon
            errored_builds_menu.set_callback(None)
            return
        self.icon = red_icon
        errored_builds_menu.set_callback(lambda sender: None)
        for b in self.last_errored_builds:
            parsed_build_finish = dateutil.parser.parse(b["finished"])
            time_since_build = dateutil.relativedelta.relativedelta(
                datetime.datetime.now(parsed_build_finish.tzinfo), parsed_build_finish
            )
            ago = f"{time_since_build.hours} {p.plural('hour', time_since_build.hours)}, {time_since_build.minutes} {p.plural('minute', time_since_build.minutes)} ago"
            if time_since_build.days:
                ago = f"{time_since_build.days} {p.plural('day', time_since_build.days)}, {ago}"
            build_id = b["id"]
            errored_builds_menu[build_id] = ErroredBuildsMenuItem(build_id, b, self)
            errored_builds_menu[build_id].title = f"{b['repoName']} - {b['branchName']} - {ago}"


if __name__ == "__main__":
    CodefreshStatusApp().run()
