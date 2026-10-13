"""Run Quickz.

    python -m quickz                          start the server
    python -m quickz reset-password USERNAME  emergency reset (prints a one-time temporary password)
"""

import sys

import segno
import uvicorn

from . import auth
from .config import load_settings
from .db import Database
from .server import create_app

BAR = "=" * 66


def reset_password(args: list[str]) -> int:
    if len(args) != 1:
        print("Usage: python -m quickz reset-password USERNAME")
        return 2
    settings = load_settings()
    db = Database(settings.db_path)
    user = db.get_user_by_username(args[0].strip().lower())
    if not user:
        print(f"No account named '{args[0]}'.")
        return 1
    temp = auth.temp_password()
    db.update_user(user["id"], password_hash=auth.hash_password(temp), must_change=1, disabled=0)
    db.delete_user_sessions(user["id"])
    print(f"\nPassword reset for {user['display_name']} ({user['username']}, {user['role']}).")
    print(f"Temporary password: {temp}")
    print("They will be asked to choose a new password at next sign-in. All their sessions were ended.\n")
    return 0


def serve() -> None:
    settings = load_settings()
    app = create_app(settings)
    print(f"\n{BAR}\n  Quickz is starting\n{BAR}")
    print(f"  Host console : {settings.public_url}/host")
    print(f"  Data folder  : {settings.data_dir}")
    token = app.state.setup_token
    if token:
        link = f"{settings.public_url}/host#/setup/{token}"
        print("\n  FIRST-TIME SETUP - create the Super Admin account")
        print(f"  Setup token  : {token}")
        print(f"  Setup link   : {link}")
        print("  (Open the link on this computer. The token works once and is only valid until you finish setup.)")
        try:
            segno.make(link, error="l").terminal(compact=True)
        except Exception:
            pass
    else:
        print("\n  Sign in with your host account. Forgot the Super Admin password?")
        print("  Run:  python -m quickz reset-password USERNAME")
    print("\n  Players scan the QR code on the projector screen to join - no sign-in needed.")
    print(BAR)
    sys.stdout.flush()
    uvicorn.run(
        app, host=settings.host, port=settings.port, log_level="warning",
        proxy_headers=settings.trust_proxy, forwarded_allow_ips="*" if settings.trust_proxy else None,
        timeout_keep_alive=30, ws_max_size=16 * 1024,   # players only ever send tiny JSON messages
    )


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "reset-password":
        raise SystemExit(reset_password(argv[1:]))
    serve()


if __name__ == "__main__":
    main()
