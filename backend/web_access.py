"""Browser sign-up, sign-in and access checks for hosted Mateo."""

from html import escape
from typing import Annotated
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from backend import admin_accounts, web_accounts


router = APIRouter(include_in_schema=False)
ADMIN_COOKIE = "__Host-mateo-web"
MEMBER_COOKIE = "__Host-mateo-member"


def safe_destination(value: str) -> str:
    try:
        destination = urlsplit(value)
    except ValueError:
        return "/"
    if destination.scheme or destination.netloc or destination.path not in {"/", "/join", "/admin"}:
        return "/"
    return urlunsplit(("", "", destination.path, destination.query, ""))


def account_page(
    destination: str,
    *,
    signup: bool = False,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    title = "Create your Mateo account" if signup else "Sign in to Mateo"
    action = "/web-access/signup" if signup else "/web-access"
    button = "Create account" if signup else "Sign in"
    password_mode = "new-password" if signup else "current-password"
    password_hint = '<small id="password-hint">Use at least 12 characters.</small>' if signup else ""
    password_constraints = ' minlength="12" aria-describedby="password-hint"' if signup else ""
    switch_label = "Already have an account?" if signup else "New to Mateo?"
    switch_action = "Sign in" if signup else "Create an account"
    switch_path = "/web-access" if signup else "/web-access/signup"
    switch_url = escape(f"{switch_path}?{urlencode({'next': destination})}", quote=True)
    error_message = f'<p role="alert">{escape(error)}</p>' if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
  background: #111827; color: #f8fafc; font: 16px system-ui, sans-serif; }}
main {{ box-sizing: border-box; width: min(100% - 2rem, 24rem); padding: 2rem;
  background: #1f2937; border: 1px solid #374151; border-radius: 1rem; }}
h1 {{ margin-top: 0; }}
label {{ display: grid; gap: .35rem; margin-bottom: 1rem; }}
input, button {{ box-sizing: border-box; width: 100%; padding: .7rem;
  border: 1px solid #64748b; border-radius: .35rem; font: inherit; }}
button {{ background: #86efac; color: #111827; font-weight: 650; cursor: pointer; }}
[role=alert] {{ color: #fca5a5; }}
a {{ color: #93c5fd; }}
.switch {{ margin: 1.25rem 0 0; }}
small {{ color: #cbd5e1; }}
</style>
</head>
<body>
<main>
<h1>{title}</h1>
{error_message}
<form method="post" action="{action}">
  <input type="hidden" name="next_url" value="{escape(destination, quote=True)}">
  <label>Email <input name="email" type="email" autocomplete="username" maxlength="254" required></label>
  <label>Password <input name="password" type="password" autocomplete="{password_mode}" maxlength="256"{password_constraints} required>{password_hint}</label>
  <button>{button}</button>
</form>
<p class="switch">{switch_label} <a href="{switch_url}">{switch_action}</a></p>
</main>
</body>
</html>""",
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
        },
    )


@router.get("/web-access")
@router.get("/web-access/signup")
def web_login(request: Request):
    return account_page(
        safe_destination(request.query_params.get("next", "/")),
        signup=request.url.path == "/web-access/signup",
    )


def require_same_origin(request: Request):
    if request.headers.get("origin") != f"https://{request.url.netloc}":
        raise HTTPException(status_code=403, detail="Invalid account request origin")


def account_redirect(destination: str) -> RedirectResponse:
    return RedirectResponse(destination, status_code=303, headers={"Cache-Control": "no-store"})


def clear_cookie(response: Response, cookie_name: str):
    response.delete_cookie(cookie_name, secure=True, httponly=True, samesite="lax")


def signed_in(destination: str, cookie_name: str, token: str, ttl: int) -> RedirectResponse:
    response = account_redirect(destination)
    other_cookie = ADMIN_COOKIE if cookie_name == MEMBER_COOKIE else MEMBER_COOKIE
    clear_cookie(response, other_cookie)
    response.set_cookie(
        cookie_name,
        token,
        max_age=ttl,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/web-access")
@router.post("/web-access/signup")
def web_login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_url: Annotated[str, Form()] = "/",
):
    require_same_origin(request)
    destination = safe_destination(next_url)
    signup = request.url.path == "/web-access/signup"
    previous_session = request.cookies.get(MEMBER_COOKIE)
    try:
        email = admin_accounts.normalize_email(email)
        if signup:
            admin_accounts.validate_password(password)
        elif len(password) > 256:
            raise HTTPException(status_code=422, detail="Invalid email or password")
    except HTTPException as exc:
        return account_page(destination, signup=signup, error=exc.detail, status_code=exc.status_code)

    if signup:
        account_id = web_accounts.register(email, password)
        if account_id is None:
            return account_page(
                destination,
                signup=True,
                error="This email already has an account. Sign in to continue.",
                status_code=409,
            )
    else:
        account_id = web_accounts.authenticate(email, password)
        if account_id is None:
            try:
                session = admin_accounts.login(admin_accounts.LoginRequest(email=email, password=password))
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                return account_page(destination, error="Invalid email or password.", status_code=401)
            web_accounts.end_session(previous_session)
            return signed_in(destination, ADMIN_COOKIE, session["access_token"], session["expires_in"])

    if destination == "/admin" or destination.startswith("/admin?"):
        destination = "/"
    token = web_accounts.create_session(account_id, previous_session)
    return signed_in(destination, MEMBER_COOKIE, token, web_accounts.SESSION_TTL)


@router.post("/web-access/logout")
def web_logout(request: Request):
    require_same_origin(request)
    web_accounts.end_session(request.cookies.get(MEMBER_COOKIE))
    response = account_redirect("/web-access")
    clear_cookie(response, ADMIN_COOKIE)
    clear_cookie(response, MEMBER_COOKIE)
    return response


@router.get("/_mateo_sign_in")
def redirect_to_sign_in(original_uri: Annotated[str, Header(alias="X-Mateo-Return")] = "/"):
    destination = safe_destination(original_uri)
    return RedirectResponse(
        f"/web-access?{urlencode({'next': destination})}",
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


def has_admin_session(token: str | None) -> bool:
    if not token:
        return False
    try:
        admin_accounts.require_admin(f"Bearer {token}")
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return False
    return True


@router.get("/_mateo_auth")
def check_web_access(request: Request):
    authorization = request.headers.get("authorization", "")
    using_bearer = authorization.startswith("Bearer ")
    token = authorization[7:] if using_bearer else request.cookies.get(ADMIN_COOKIE)
    if has_admin_session(token):
        return Response(status_code=204)
    if not using_bearer and web_accounts.has_session(request.cookies.get(MEMBER_COOKIE)):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    raise HTTPException(status_code=401, detail="Sign in to Mateo")


@router.get("/_mateo_member_auth")
def check_meeting_access(request: Request):
    if web_accounts.has_session(request.cookies.get(MEMBER_COOKIE)) or has_admin_session(
        request.cookies.get(ADMIN_COOKIE)
    ):
        return Response(status_code=204)
    raise HTTPException(status_code=401, detail="Sign in to Mateo")
