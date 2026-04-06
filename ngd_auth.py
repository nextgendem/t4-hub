import logging
import os

from oauthenticator.google import GoogleOAuthenticator
from tornado import gen
import requests
from requests.exceptions import RequestException


def get_user_roles(email, protocol_server=None):
    if not protocol_server:
        protocol_server = os.environ.get("NGD_PROTOCOL_SERVER", "http://172.17.0.1:5000")
    try:
        response = requests.get(
            f"{protocol_server}/api/user_roles",
            params={'email': email},
            headers={'Cache-Control': 'no-cache'}
        )
        response.raise_for_status()
    except RequestException as e:
        print(f"A network error occurred: {e}")
        return []
    except requests.exceptions.HTTPError as e:
        print(f"An HTTP error occurred: {e}")
        return []
    else:
        return response.json()["roles"]


def append_to_file(content, file_path="/tmp/ngd_auth.log"):
    with open(file_path, "a") as f:
        from datetime import datetime
        f.write(f'{datetime.now().strftime("%Y/%m/%d %H:%M:%S")}: {content}\n')


class NgdAuthenticator(GoogleOAuthenticator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(__name__)
        self.ngd_protocol_server = None

    async def check_allowed(self, username, auth_model):
        """
        Overrides the OAuthenticator.check_allowed to also allow users part of
        `allowed_google_groups`.
        """
        from tornado.web import HTTPError
        user_info = auth_model["auth_state"][self.user_auth_state_key]
        user_email = user_info["email"]
        user_domain = user_info["domain"]

        if not user_info["verified_email"]:
            message = f"Login with unverified email {user_email} is not allowed"
            self.log.warning(message)
            raise HTTPError(403, message)

        if self.hosted_domain:
            if user_domain not in self.hosted_domain:
                message = f"Login with domain @{user_domain} is not allowed"
                self.log.warning(message)
                raise HTTPError(403, message)

        user_roles = get_user_roles(user_email, self.ngd_protocol_server)
        is_authorized = "jupyterlab" in user_roles or "jupyterhub" in user_roles
        if is_authorized:
            return True

        # if await super().check_allowed(username, auth_model):
        #     return True

        return False