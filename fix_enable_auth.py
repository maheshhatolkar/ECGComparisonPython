import ECGComparisonPython as app
from db import set_setting, get_setting, init_db

if __name__ == "__main__":
    init_db()
    set_setting("user_management_enabled", "true")
    print("DB Setting user_management_enabled:", get_setting("user_management_enabled"))
    print("App is_user_management_enabled():", app.is_user_management_enabled())
    print("App authenticate_user('admin', 'admin'):", app.authenticate_user('admin', 'admin'))
