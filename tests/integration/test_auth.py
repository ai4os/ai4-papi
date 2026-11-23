from conf import token

from ai4papi import auth

auth_info = auth.get_user_info(token)
auth.check_authorization(auth_info, "vo.ai4eosc.eu", "ap-u")

print("🟢 Auth tests passed!")
