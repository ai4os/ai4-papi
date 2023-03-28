from flaat.config import AccessLevel
from flaat.requirements import CheckResult, HasSubIss, IsTrue

ADMIN_EMAILS = []
def is_admin(user_infos):
    return user_infos.user_info["email"] in ADMIN_EMAILS


def init_flaat(my_flaat):
    my_flaat.set_access_levels(
        [
            AccessLevel("user", HasSubIss()),
            AccessLevel("admin", IsTrue(is_admin)),
        ]
    )

    my_flaat.set_trusted_OP_list(
        [
            "https://aai-demo.egi.eu/oidc",
            "https://aai-demo.egi.eu/auth/realms/egi",
            "https://aai-dev.egi.eu/oidc",
            "https://aai.egi.eu/oidc/",
            "https://aai.egi.eu/auth/realms/egi",
            "https://accounts.google.com/",
            "https://b2access-integration.fz-juelich.de/oauth2",
            "https://b2access.eudat.eu/oauth2/",
            "https://iam-test.indigo-datacloud.eu/",
            "https://iam.deep-hybrid-datacloud.eu/",
            "https://iam.extreme-datacloud.eu/",
            "https://login-dev.helmholtz.de/oauth2/",
            "https://login.elixir-czech.org/oidc/",
            "https://login.helmholtz-data-federation.de/oauth2/",
            "https://login.helmholtz.de/oauth2/",
            "https://oidc.scc.kit.edu/auth/realms/kit/",
            "https://orcid.org/",
            "https://proxy.demo.eduteams.org",
            "https://services.humanbrainproject.eu/oidc/",
            "https://unity.eudat-aai.fz-juelich.de/oauth2/",
            "https://unity.helmholtz-data-federation.de/oauth2/",
            "https://wlcg.cloud.cnaf.infn.it/",
            "https://proxy.eduteams.org/",
        ]
    )
