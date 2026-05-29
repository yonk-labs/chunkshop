"""Service class for RM-C parity test."""


class UserService:
    def __init__(self, db):
        self.db = db

    def get_user(self, user_id):
        return self.db.find(user_id)

    def list_users(self):
        return list(self.db.iter())
