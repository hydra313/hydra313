# Background scanner and desktop launcher

The Windows launcher opens ConsoleHawk in a dedicated app window. The server now
starts an extension-free collector using installed Microsoft Edge headlessly.
Each source has its own local browser profile. Searches do not create tabs in the
user's ordinary Chrome or Edge session.

Connections shows live per-site states and provides Sign in / Resume scanning.
Sign in opens a separate visible session for the user. Resume closes it, keeps
its browser-managed login, and returns that site to hidden scanning. A site that
needs verification pauses without repeated pop-ups; other sources keep running.
No password capture, automated sign-in, challenge solving, bids or purchases.
The previous extension's search-job requests are paused by the local server.

Live validation: HiBid completed a hidden scan with 22 usable records. eBay needs
attention; its dedicated sign-in window was opened. The user chose to sign in later.
Live background eBay active/sold collection remains unverified until that sign-in
and a successful capture. Site restrictions may still prevent hidden collection.

Tests: 1,086 Python tests passed. Four separate opt-in tests ran the shipped card
capture code in real headless Edge against local PS2, Halo auction, HiBid and sold
fixtures. They confirm parsable records and exclude injected account/password data.
Worker tests cover hidden-only launch, sandboxing, separate profiles, failure
isolation, manual sign-in/resume, and context cleanup. Web tests confirm the old
extension cannot start jobs and the existing capability/CSRF checks remain enforced.

Private `.collector-profile/`, `.desktop-profile/`, SQLite, capability and protected
Discord credentials are excluded from source packages. Existing data is preserved.
The dashboard can be closed while the server and scanner keep running. The PC
must remain awake. No paid service or API key is required.
