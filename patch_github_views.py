import sys
import traceback

def test_import():
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
        return True
    except Exception as e:
        traceback.print_exc()
        return False

print(test_import())
