"""Sample Python file for RM-C cross-port parity test."""

def hello():
    return "hello"


class Greeter:
    def greet(self, name):
        return f"hello, {name}"

    def shout(self, name):
        return self.greet(name).upper()
