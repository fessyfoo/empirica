"""
Quick test of epistemic components
"""

import asyncio

from empirica_mcp.epistemic import (
    EpistemicModes,
    EpistemicStateMachine,
    VectorRouter,
    get_personality,
    list_personalities,
)


async def test_state_machine():
    print("\n=== Testing EpistemicStateMachine ===")

    sm = EpistemicStateMachine()
    print(f"Initial state: {sm}")

    # Assess a complex request
    vectors = sm.assess_request("Design and integrate a new authentication system")
    print(f"After complex request: know={vectors['know']:.2f}, uncertainty={vectors['uncertainty']:.2f}")

    # Update from successful investigation
    vectors = sm.update_from_action("investigate", {"success": True})
    print(f"After investigation: know={vectors['know']:.2f}, uncertainty={vectors['uncertainty']:.2f}")

    # Update from implementation
    vectors = sm.update_from_action("implement", {"success": True})
    print(f"After implementation: completion={vectors['completion']:.2f}, impact={vectors['impact']:.2f}")


def test_router():
    print("\n=== Testing VectorRouter ===")

    # Test with cautious personality
    cautious = get_personality("cautious_researcher")
    router = VectorRouter(cautious.thresholds)

    # Low context scenario
    vectors = {
        "clarity": 0.8,
        "context": 0.3,
        "uncertainty": 0.5,
        "know": 0.5
    }
    decision = router.route(vectors, "Some request")
    print("\nLow context routing:")
    print(router.explain_routing(decision))

    # High uncertainty scenario
    vectors = {
        "clarity": 0.7,
        "context": 0.6,
        "uncertainty": 0.8,
        "know": 0.5
    }
    decision = router.route(vectors, "Complex task")
    print("\nHigh uncertainty routing:")
    print(router.explain_routing(decision))

    # High confidence scenario
    vectors = {
        "clarity": 0.9,
        "context": 0.8,
        "uncertainty": 0.2,
        "know": 0.9
    }
    decision = router.route(vectors, "Simple task")
    print("\nHigh confidence routing:")
    print(router.explain_routing(decision))


async def test_modes():
    print("\n=== Testing EpistemicModes ===")

    modes = EpistemicModes()

    # Test investigate mode
    result = await modes.investigate("test-session", "authentication flow")
    print("\nInvestigate mode:")
    print(result["guidance"][:200])

    # Test confident implementation
    result = await modes.confident_implementation("test-session", "add feature")
    print("\nConfident implementation:")
    print(result["guidance"][:200])


def test_personalities():
    print("\n=== Testing Personalities ===")

    personalities = list_personalities()
    for name, profile in personalities.items():
        print(f"\n{name}:")
        print(f"  {profile['description']}")
        print(f"  Thresholds: {profile['thresholds']}")


async def main():
    print("🧠 Epistemic MCP Components Test")
    print("=" * 60)

    await test_state_machine()
    test_router()
    await test_modes()
    test_personalities()

    print("\n✅ All components working!")


if __name__ == "__main__":
    asyncio.run(main())
