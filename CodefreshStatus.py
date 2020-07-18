# TODO: logging somehow
# TODO: actually handle errors...

import rumps, requests, enum, webbrowser, datetime, dateutil, dateutil.parser, inflect, tinydb

rumps.debug_mode(True)

cf_build_template = "https://g.codefresh.io/build/%(id)s"
cf_root = "https://g.codefresh.io/api/workflow"
# TODO: relative path resolution
red_icon = "/Users/mhill/Projects/Personal/cfstatus/red.png"
green_icon = "/Users/mhill/Projects/Personal/cfstatus/green.png"
db_path = "/Users/mhill/Projects/Personal/cfstatus/db.json"

p = inflect.engine()
db = tinydb.TinyDB(db_path)

ignore_build_ids = db.table("ignore_build_ids")
settings = db.table("settings")


class CallableEnum(enum.Enum):
    def __call__(self):
        return self.value


class TimePeriods(CallableEnum):
    today = "Today"
    this_week = "This Week"
    this_month = "This Month"


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
        self["View"] = rumps.MenuItem("View", callback=self._view_build)
        # TODO:(idea): if we restart a build, it would be cool to check if it succeeded
        # when we look for builds, and if so, auto-mark it fixed.
        self["Restart"] = rumps.MenuItem("Restart")  # TODO: handler
        self["Mark Fixed"] = rumps.MenuItem("Mark Fixed", callback=self._mark_fixed)

    def _view_build(self, sender):
        webbrowser.open(cf_build_template % self.build)

    def _mark_fixed(self, sender):
        print("ignoring build with id " + self.build["id"])
        ignore_build_ids.insert({"build_id": self.build["id"]})
        del self.app.menu[Menus.errored_builds()][self.build["id"]]
        self.app.last_errored_builds.remove(self.build)
        self.app._update_errored_builds_menu()


class SettingsMenu(rumps.MenuItem):
    def __init__(self):
        super().__init__(Menus.settings())
        # TODO: the weird 'next(iter(...))' logic is copypasted from _update_errored_builds_menu.. need to clean this up
        self["Codefresh API Key"] = rumps.MenuItem(
            "Codefresh API Key",
            callback=self._gather_user_input(
                "Codefresh API Key", "The Codefresh API key to authenticate with.", "codefresh_api_key",
            ),
        )
        self["Github Username"] = rumps.MenuItem(
            "Github Username",
            callback=self._gather_user_input(
                "Github Username", "The Github username used to filter builds.", "github_username",
            ),
        )

    def _gather_user_input(self, title, message, db_key):
        def _gather(sender):
            all_settings = settings.all()
            response = rumps.Window(
                title=title,
                message=message,
                default_text=next(iter([s for s in all_settings if s.get(db_key)]), {}).get(db_key, ""),
                cancel=True,
            ).run()
            print(response)
            if response.clicked:
                print(f"updating settings[{db_key}] with {response.text}")
                settings.update({db_key: response.text})

        return _gather


class CodefreshStatusApp(rumps.App):
    last_errored_builds = []

    def __init__(self):
        super().__init__(
            "Codefresh Status",
            icon=green_icon,
            menu=[ErroredBuildsMenu(), rumps.separator, TimePeriodMenu(self), SettingsMenu(), rumps.separator],
        )

    # TODO: this runs in a separate thread, might need to add thread safety logic
    @rumps.timer(10)  # TODO: setting
    def _get_cf_builds(self, sender):
        all_settings = settings.all()
        codefresh_api_key = next(iter([s for s in all_settings if s.get("codefresh_api_key")]), {}).get(
            "codefresh_api_key", None
        )
        github_username = next(iter([s for s in all_settings if s.get("github_username")]), {}).get(
            "github_username", None
        )
        if not codefresh_api_key or not github_username:
            print("missing required setting(s): codefresh_api_key, github_username")
            self.icon = red_icon
            self.title = "!! Missing Required Settings !!"
            return
        self.title = None
        print("getting cf builds")
        response = requests.get(
            cf_root,
            headers={"Authorization": codefresh_api_key},
            params={
                "inlineView[filters][0][selectedValue]": "type",
                "inlineView[filters][0][findType]": "is",
                "inlineView[filters][0][values][0]": "webhook",
                "inlineView[filters][0][values][1]": "build",
                "inlineView[filters][1][selectedValue]": "committer",
                "inlineView[filters][1][findType]": "is",
                "inlineView[filters][1][values][0]": github_username,
                "inlineView[type]": "build",
                "inlineView[timeFrameStart][0]": self.menu[Menus.time_period()].selected_time_period,
                "limit": 99999999,
            },
        )
        print("got cf builds")
        response.raise_for_status()
        body = response.json()
        self.last_errored_builds = self._builds_with_errors(body)
        if self.last_errored_builds:
            # TODO: need to track build IDs we've notified about so we dont spam notifications every 10s
            rumps.notification(title="title", subtitle="subtitle", message="message", sound=True)
        self._update_errored_builds_menu()

    def _builds_with_errors(self, body) -> bool:
        builds = body["workflows"]["docs"]
        all_errored_builds = [b for b in builds if b["status"] == "error" and b["id"]]
        print(f"found {len(all_errored_builds)} builds with errors")
        filtered_errored_builds = [
            b for b in all_errored_builds if not ignore_build_ids.contains(tinydb.where("build_id") == b["id"])
        ]
        print(f"filtered {len(all_errored_builds) - len(filtered_errored_builds)} builds with errors")
        return filtered_errored_builds

    def _update_errored_builds_menu(self):
        print("building errored builds menu")
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
