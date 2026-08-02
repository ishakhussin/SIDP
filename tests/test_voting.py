import unittest

from sentrylab.domain.detection import SafetyLevel
from sentrylab.services.voting import MajorityVoteState


class MajorityVoteStateTest(unittest.TestCase):
    def test_three_warning_votes_become_unsafe(self):
        state = MajorityVoteState()
        self.assertEqual(
            state.add_vote(SafetyLevel.WARNING, 0), SafetyLevel.WARNING
        )
        votes = [
            SafetyLevel.WARNING,
            SafetyLevel.WARNING,
            SafetyLevel.SAFE,
            SafetyLevel.WARNING,
            SafetyLevel.SAFE,
        ]
        for second, vote in enumerate(votes, start=1):
            state.add_vote(vote, second)
        self.assertEqual(state.level, SafetyLevel.UNSAFE)
        self.assertEqual(len(state.votes), 0)

    def test_unknown_votes_do_not_count_as_safe(self):
        state = MajorityVoteState()
        state.add_vote(SafetyLevel.WARNING, 0)
        votes = [
            SafetyLevel.WARNING,
            SafetyLevel.UNKNOWN,
            SafetyLevel.UNKNOWN,
            SafetyLevel.SAFE,
        ]
        for second, vote in enumerate(votes, start=1):
            state.add_vote(vote, second)
        self.assertEqual(state.level, SafetyLevel.WARNING)
        self.assertEqual(len(state.votes), 2)

    def test_three_safe_votes_recover_from_unsafe(self):
        state = MajorityVoteState()
        state.add_vote(SafetyLevel.WARNING, 0)
        for second, vote in enumerate([
            SafetyLevel.WARNING,
            SafetyLevel.WARNING,
            SafetyLevel.WARNING,
            SafetyLevel.SAFE,
            SafetyLevel.SAFE,
        ], start=1):
            state.add_vote(vote, second)
        self.assertEqual(state.level, SafetyLevel.UNSAFE)

        recovery = [
            SafetyLevel.SAFE,
            SafetyLevel.SAFE,
            SafetyLevel.SAFE,
            SafetyLevel.UNKNOWN,
            SafetyLevel.UNKNOWN,
        ]
        for second, vote in enumerate(recovery, start=6):
            state.add_vote(vote, second)
        self.assertEqual(state.level, SafetyLevel.UNSAFE)
        self.assertEqual(len(state.votes), 3)
        state.add_vote(SafetyLevel.SAFE, 11)
        state.add_vote(SafetyLevel.SAFE, 12)
        self.assertEqual(state.level, SafetyLevel.SAFE)
        self.assertEqual(len(state.votes), 0)

    def test_track_expires_after_two_seconds(self):
        state = MajorityVoteState()
        state.add_vote(SafetyLevel.WARNING, 10.0)
        self.assertFalse(state.is_expired(12.0))
        self.assertTrue(state.is_expired(12.01))

    def test_new_violation_discards_all_previous_safe_history(self):
        state = MajorityVoteState()
        for second in range(1, 6):
            state.add_vote(SafetyLevel.SAFE, second)
        self.assertEqual(state.level, SafetyLevel.SAFE)
        self.assertEqual(state.add_vote(SafetyLevel.WARNING, 6), SafetyLevel.WARNING)
        self.assertEqual(len(state.votes), 0)
        for second in range(7, 11):
            self.assertEqual(
                state.add_vote(SafetyLevel.WARNING, second), SafetyLevel.WARNING
            )
        self.assertEqual(state.add_vote(SafetyLevel.WARNING, 11), SafetyLevel.UNSAFE)

    def test_two_violation_and_three_safe_votes_return_to_safe(self):
        state = MajorityVoteState()
        state.add_vote(SafetyLevel.WARNING, 0)
        for second, vote in enumerate([
            SafetyLevel.WARNING,
            SafetyLevel.SAFE,
            SafetyLevel.WARNING,
            SafetyLevel.SAFE,
            SafetyLevel.SAFE,
        ], start=1):
            state.add_vote(vote, second)
        self.assertEqual(state.level, SafetyLevel.SAFE)
        self.assertEqual(len(state.votes), 0)


if __name__ == "__main__":
    unittest.main()
