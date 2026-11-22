# FlowXav

[Français](README.md) · **English**

FlowXav teaches you how to start an airliner in Microsoft Flight Simulator,
**in the real cockpit**. For each step, the camera moves to the right
control, an orange ring blinks on it, and FlowXav waits for you to operate
it. As soon as the aircraft changes state, the ring turns green and the next
step begins.

You don't need to know the cockpit beforehand, or read a manual on a second
screen. FlowXav shows, you operate.

> For simulation only: never use FlowXav for real flight.

## What FlowXav does

- **Step-by-step guidance**: the camera shows the control, a ring blinks on
  it, and the step validates itself when the aircraft state matches.
- **"Show me"**: search a control (BAT, IRS, APU…) and FlowXav shows it for a
  few seconds.
- **Aircraft recognition** of the aircraft loaded in MSFS, from its title.
- **One state source per aircraft**: the developer's SDK when there is one
  (PMDG), otherwise simulator variables.

## Supported aircraft

| Aircraft | State | Control map |
|---|---|---|
| PMDG 737-900 (MSFS 2024) | PMDG SDK | 289 controls: overhead, aft overhead (IRS), centre panel (MCP, EFIS), pedestal, left CDU |

More aircraft, Boeing and Airbus alike, will come by adding their profiles.
For the PMDG 737, the shipped procedure is a **demo** of the first power-up
steps for now. The full Cold & Dark to ready-for-departure procedure is
being written.

## Requirements

- Windows 10 or 11, 64-bit.
- Microsoft Flight Simulator 2024 in **windowed** or **borderless fullscreen**
  mode. The ring does not show in exclusive fullscreen or in VR.
- For the PMDG 737: the SDK broadcast must be enabled. In the aircraft's
  `737_Options.ini`
  (`%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalState\WASM\MSFS2024\pmdg-aircraft-739\work`),
  add these lines, then reload the aircraft:

  ```ini
  [SDK]
  EnableDataBroadcast=1
  ```

## Installation

Download `FlowXav-Setup-<version>.exe` from the
[releases](https://github.com/xalacaga/FlowXav/releases) and run it. The
installer:

- installs FlowXav for your account, without administrator rights, in
  `%LOCALAPPDATA%\Programs\FlowXav`;
- installs Microsoft WebView2 if it is missing;
- never installs or replaces the system SimConnect: FlowXav carries its own
  copy of the library, which talks to MSFS.

A portable archive (`FlowXav-<version>-windows-x64-portable.zip`) is also
available.

## Usage

1. Start MSFS and load your aircraft, preferably Cold & Dark.
2. Start FlowXav: it connects by itself and recognises the aircraft.
3. Choose a procedure, then **Start guidance**.
4. Operate the control circled by the ring. During guidance, the view stays
   locked on the control: this is intended, and clicks still go through.
5. **Done** validates a step manually, for example a push button whose state
   cannot be read. **Stop** gives the camera back.

Your settings and the log are in `%LOCALAPPDATA%\FlowXav`.

## Privacy

FlowXav runs entirely on your computer. Its local service only listens on
`127.0.0.1` and nothing is sent to the Internet.

## Contributors

See [CONTRIBUTING.md](CONTRIBUTING.md): architecture, adding an aircraft,
mapping a cockpit and building the installer.

## Licence

PolyForm Noncommercial 1.0.0: see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Microsoft Flight Simulator, PMDG, Boeing and Airbus are trademarks of their
respective owners. FlowXav is not affiliated with any of them.
