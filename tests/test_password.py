from __future__ import annotations

from arcane_mage.password import HashedPassword


class TestHashedPassword:
    def test_hash_produces_yescrypt_string(self):
        hp = HashedPassword(password="testpassword123")
        hashed = hp.hash()

        assert hashed.startswith("$y$")

    def test_hash_is_deterministic_with_same_salt(self):
        hp = HashedPassword(password="testpassword123")

        hash1 = hp.hash()
        hash2 = hp.hash()

        assert hash1 == hash2

    def test_different_passwords_different_hashes(self):
        hp1 = HashedPassword(password="password1")
        hp2 = HashedPassword(password="password2")

        assert hp1.hash() != hp2.hash()

    def test_validate_correct_password(self):
        hp = HashedPassword(password="correct-password")
        hashed = hp.hash()

        assert hp.validate(hashed.encode())

    def test_validate_wrong_password(self):
        hp = HashedPassword(password="correct-password")
        hashed = hp.hash()

        wrong_hp = HashedPassword(password="wrong-password")

        assert not wrong_hp.validate(hashed.encode())
