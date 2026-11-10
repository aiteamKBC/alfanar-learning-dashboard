"""Keep the dashboard and the independent Lecture Library on separate databases."""
LIBRARY_ALIAS = 'lecture_library'
LEGACY_LIBRARY_MODELS = {'lecturecapture', 'lecturelearner', 'lectureresource'}


class DatabaseRouter:
    def db_for_read(self, model, **hints):
        return LIBRARY_ALIAS if model._meta.app_label == LIBRARY_ALIAS else 'default'

    db_for_write = db_for_read

    def allow_relation(self, obj1, obj2, **hints):
        # request.user may be a SimpleLazyObject; its _meta proxies to the user.
        return self.db_for_read(obj1) == self.db_for_read(obj2)

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == LIBRARY_ALIAS:
            return db == LIBRARY_ALIAS
        if db != 'default':
            return False
        # Historical library migrations must never create library tables in SQLite.
        if app_label == 'analytics' and model_name in LEGACY_LIBRARY_MODELS:
            return False
        return True
