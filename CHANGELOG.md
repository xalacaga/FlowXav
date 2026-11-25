# Changelog

## 0.1.0 (unreleased)

### Added

- Step-by-step cockpit guidance: the camera shows the control, an orange ring blinks on it over Microsoft Flight Simulator, and the step validates itself when the aircraft reaches the expected state.
- The camera is yours as soon as you move: FlowXav first places the view facing the panel, then gives the camera back to MSFS the moment you press the right or middle mouse button or turn the wheel over MSFS. You then look around and zoom with your usual MSFS controls; the ring follows the control from wherever you look, and an arrow at the screen edge points to it when it is out of view.
- Ground state read from the aircraft: wheel chocks, every passenger, service and cargo door, and GSX Pro. New steps validate by themselves so nothing is forgotten before moving: doors and holds closed, chocks removed (and set again at the end of the flight); "Check start state" also checks the chocks.
- Pushback with engines off, before engine start: with GSX Pro (request in GSX, release the parking brake when the tug is connected, wait for the end, set the parking brake) or without GSX (simulator pushback). Steps that only apply with or without GSX are skipped by themselves.
- Exterior lights through the whole flight: strobes, landing, runway turnoff lights and TCAS TA/RA when lining up; turnoff lights off and on with the landing lights in climb and descent; turnoff lights off and strobes to steady after landing; position lights off at the end of the flight. "Do it" also handles the runway turnoff, logo and wing lights.
- "Show me": search any mapped control and FlowXav shows it for a few seconds.
- Automatic connection to MSFS 2024 and aircraft recognition from its title.
- PMDG 737-900 profile: 409 controls mapped across seven views (overhead, aft overhead, centre panel, pedestal, aft pedestal, left CDU close-up), with state read through the PMDG SDK.
- First power-up demo procedure for the PMDG 737-900.
- Full PMDG 737-900 flow from Cold & Dark to ready for taxi, in four procedures: power-up and APU, cockpit preparation, FMC programming, before start / engine start / taxi.
- A step on a stateless control can validate itself on another state field (a light, FMC data), e.g. V1/VR/V2 accepted on the CDU or the parking brake light.
- FlowXav follows the aircraft: steps already done in the cockpit are skipped, a procedure resumes where the aircraft is, and the procedure list preselects the one that follows the last completed procedure.
- "Next procedure" button at the end of a procedure to chain straight into the following one.
- Known start state: "Reset to Cold & Dark" reloads the PMDG COLDDARK panel state by driving the CDU (MENU > PMDG SETUP > PANEL STATE LOAD, reading the screen at each page), then "Check start state" lists what is still wrong, including what a panel state cannot set: thrust levers following a physical throttle, parking brake, flaps, speedbrake.
- SimBrief import: enter your SimBrief identifier once, click "Import my latest plan" and FlowXav places the route in the aircraft (PMDG .rte, ready for RTE > FLT PLAN REQUEST) and shows the figures to enter in the FMC (cruise level, cost index, ZFW, reserves, runways, SID). SimBrief is only contacted when you click.
- New interface: a journey of the procedures (done / suggested / to do) with one big Start button; during guidance the step fills the screen with a segmented progress bar, the next steps and keyboard shortcuts (Space or Enter = Done, arrows = previous / next); a Briefing side panel for the start state and the SimBrief flight (click a value to copy it for the FMC); "Show me" as a Ctrl+K search palette.
- Countdowns for waits (APU start, starter cutout): the countdown starts when the start is detected (APU EGT rising) and learns the real duration in your aircraft for the next time.
- Modern on-screen marker: a thin ring with a centre dot and a fading sonar wave, a small dark label pill in a lighter font, and a thin chevron at the screen edge when the control is out of view.
- "Done" only validates manual steps; steps checked against the aircraft wait for its real state ("Next" still skips on purpose).
- Ground power option: phase 1 comes in two variants, on APU or on ground power (GPU); "Reset to Cold & Dark + GPU" loads the PMDG COLDDARKGPU state; procedure 4 starts the APU before the engines and has the GPU disconnected before taxi, both skipped by themselves when already done.
- The ring follows the CDU screen: a step can target a text on the screen (LOAD, the SimBrief runway or SID) and the ring moves to the key facing it. New FMC step: LOAD the uplinked SimBrief route.
- The on-screen label sits beside the ring and fades after 4 seconds, so it never hides neighbouring controls.
- Your flight values on the steps: the SimBrief plan (cruise level, cost index, runway, SID, reserves, QNH from the METAR, destination elevation) and live FMC data (V2, takeoff flaps) are shown on each step; MCP speed and heading, FLT ALT and LAND ALT validate themselves when set to your flight's value.
- "Do it" (F key): FlowXav operates the step's control itself through the PMDG SDK (direct switch positions, guards lifted and closed, MCP and pressurisation values, CDU keys with the flight values typed in), then checks the aircraft state. Automatic mode performs every doable step without taking the camera. Fuel levers, thrust levers, flaps, speedbrake and spring-loaded switches always stay with the pilot.
- Second screen (off by default): from FlowXav on the PC, open the guidance on a tablet or phone of the local network. A separate service accepts only the PC's network address, requires a 6-digit pairing code shown on the PC (new code after five wrong attempts), and never exposes the SimBrief identifier nor the aircraft reset. It stops when disabled or when FlowXav closes.
- Aircraft publisher licence: as the PMDG EULA requires for applications built with its SDK (§ 1.t), FlowXav displays the agreement read from your own aircraft installation (it is never shipped with FlowXav, § 1.n/1.p) and asks for an explicit acceptance; until then it guides but operates nothing in the aircraft (no Do it, automatic mode or reset). A new agreement text must be accepted again.
- PMDG SDK tables (state layout and control events) are generated on first launch from the SDK header of the aircraft installed on your PC and cached locally (regenerated after an aircraft update); no file or table derived from PMDG files is shipped with FlowXav anymore.
- Interface in French and English.
- Whole-flight guidance after taxi: climb, cruise, descent, approach, after-landing and shutdown procedures, selected from generic simulator flight-phase data.
- Two approach variants: a manually flown landing or a monitored dual-channel autoland with APP, CMD A/B, configuration and touchdown checks.
- During approach and after landing, the camera shows the control for three seconds, then gives the view back to the pilot.
- The flight phase memory is reset when the aircraft is reloaded or a start state is restored, so a second flight starts from parking instead of end of flight.

### Fixed

- The window could stay empty after an update (no journey, no connection): the interface files now carry a fingerprint of their content in their address, so the WebView can never reuse an outdated copy.

### Security

- Hardened the local API against cross-site requests and DNS rebinding with loopback host/origin checks, a strict same-site session cookie, bounded request bodies and restrictive browser security headers.
- SimBrief downloads are HTTPS-only, restricted to SimBrief domains, size-limited and written atomically; local settings are also saved atomically.
- Aircraft profiles, procedures and PMDG layouts are size- and structure-validated before use, including path, duplicate-key, finite-number and payload-boundary checks.
- The installer can no longer request administrator privileges, and distribution builds verify dependency consistency and smoke-test the packaged executable.
- New environments require a non-vulnerable pip release, and distribution builds fail when `pip-audit` reports a known dependency vulnerability.
