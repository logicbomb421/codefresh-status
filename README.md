# Codefresh Status

A simple MacOS menubar application that will monitor your Codefresh builds and alert you when any have failed.

![](example.gif)

### Installation

1. Download the latest release from the [releases page](https://github.com/logicbomb421/codefresh-status/releases)
2. Create a [Codefresh API key](https://codefresh.io/docs/docs/integrations/codefresh-api/#authentication-instructions) (minimum scopes: `Build->Read+Write`, `Pipeline->Run`, `Workflow->Read`)
3. Start the `codefresh-status` app
4. Add your Codefresh API key to the app (`Settings->Codefresh API Key`)
5. Add your Github username to the app (`Settings->Github Username`)

### Configuration

|Option|Required|Default|Notes|
|---|---|---|---|
|`Github Username`|Yes|N/A|Your Github username (used to filter your Codefresh builds).|
|`Codefresh API Key`|Yes|N/A|A Codefresh API key with appropriate scopes (see installation step #2).|
|`Status Check Interval`|No|`10`|The number of seconds to wait between checking Codefresh build status.|
|`Notifications`|No|Enabled|Whether or not to present a notification if failed builds are detected (all builds aggregated into a single notification).|
|`Show Build on Restart`|No|Enabled|When restarting failed builds, whether or not to navigate to the new build in the default browser.|