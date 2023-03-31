"""
Authentication for private methods of the API (mainly managing deployments)
"""

from flaat.config import AccessLevel
from flaat.requirements import CheckResult, HasSubIss, IsTrue
from flaat.fastapi import Flaat

from ai4eosc.conf import MAIN_CONF


flaat:Flaat = Flaat()


def is_admin(user_infos):
    return user_infos.user_info["email"] in MAIN_CONF["auth"]["admins"]


def init_flaat():
    flaat.set_access_levels(
        [
            AccessLevel("user", HasSubIss()),
            AccessLevel("admin", IsTrue(is_admin)),
        ]
    )

    flaat.set_trusted_OP_list(MAIN_CONF["auth"]["OP"])


def get_owner(request):
    user_infos = flaat.get_user_infos_from_request(request)
    sub = user_infos.get('sub')  # this is subject - the user's ID
    iss = user_infos.get('iss')  # this is the URL of the access token issuer
    return sub, iss
