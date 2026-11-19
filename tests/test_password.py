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


class TestPasswordRules:
    def test_a_long_enough_password_is_taken(self):
        from arcane_mage.password import password_problem

        assert password_problem("correct horse battery") is None

    def test_a_short_password_is_refused(self):
        from arcane_mage.password import password_problem

        assert "12 characters" in (password_problem("elevenchars") or "")

    def test_four_of_one_character_in_a_row_are_refused(self):
        from arcane_mage.password import password_problem

        assert "consecutive" in (password_problem("aaaa-long-enough") or "")

    def test_a_hash_sets_a_password_and_a_locked_entry_does_not(self):
        from arcane_mage.password import HashedPassword, sets_a_password

        assert sets_a_password(HashedPassword(password="correct horse battery").hash())
        assert not sets_a_password("!")
        assert not sets_a_password(None)
