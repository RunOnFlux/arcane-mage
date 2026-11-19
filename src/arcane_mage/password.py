import re
import secrets
from dataclasses import dataclass, field
from itertools import groupby

from pyescrypt import Mode, WrongPassword, WrongPasswordConfiguration, Yescrypt

MIN_PASSWORD_LENGTH = 12
MAX_REPEATS = 3

# A crypt(3) hash that sets a password: "$<method>$...", in crypt's own characters.
_CRYPT_HASH = re.compile(r"^\$[A-Za-z0-9./$=,-]+$")


def password_problem(password: str) -> str | None:
    """Why a console password is refused, or None for one that is taken."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be a minimum of {MIN_PASSWORD_LENGTH} characters."
    if any(len(list(group)) > MAX_REPEATS for _, group in groupby(password)):
        return f"Only {MAX_REPEATS} consecutive repeat characters allowed."
    return None


def sets_a_password(hashed: str | None) -> bool:
    """Whether a stored hash is one a password matches: a crypt(3) string. A locked entry
    ("!", "*"), an empty one and anything else set none."""
    return bool(hashed) and _CRYPT_HASH.fullmatch(hashed) is not None


@dataclass
class HashedPassword:
    """Yescrypt password hasher for generating and validating Linux shadow-compatible hashes."""

    password: str
    hasher: Yescrypt = field(default_factory=lambda: Yescrypt(n=2**12, r=32, p=1, mode=Mode.MCF))
    salt: bytes = field(default_factory=lambda: secrets.token_bytes(16))

    @property
    def encoded_pass(self) -> bytes:
        return self.password.encode()

    def hash(self) -> str:
        hashed = self.hasher.digest(password=self.encoded_pass, salt=self.salt)
        return hashed.decode()

    def validate(self, hashed: bytes) -> bool:
        try:
            self.hasher.compare(self.encoded_pass, hashed)
        except (WrongPasswordConfiguration, WrongPassword):
            return False

        return True
