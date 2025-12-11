from ai4papi import auth
from conf import token


auth_info = auth.get_user_info(token)
auth.check_authorization(auth_info, "ai4eosc", "ap-u")

print("🟢 Auth tests passed!")
