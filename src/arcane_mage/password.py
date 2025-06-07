import secrets
from dataclasses import dataclass, field

from pyescrypt import Mode, WrongPassword, WrongPasswordConfiguration, Yescrypt


@dataclass
class HashedPassword:
    password: str
    hasher: Yescrypt = field(
        default_factory=lambda: Yescrypt(n=2**12, r=32, p=1, mode=Mode.MCF)
    )
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
