
"""
Mateo AI - Medicine RAG Development Simulator

Tests:
1. Redness and dryness
2. Irritation and peeling
3. Unusable skin image
4. Unsupported skin observations
5. Invalid development token

No Gemini calls.
No real images.
No prescriptions.
Development-only API.
"""

import getpass
import json
import sys
import urllib.error
import urllib.request


API_URL = (
    "http://127.0.0.1:8000"
    "/api/v1/medicine/dev-vision-context"
)


def create_assessment(
    features,
    image_usable=True,
):
    observations = [
        {
            "feature": feature,
            "location": "simulated test area",
            "description": (
                f"Simulated observation: {feature}"
            ),
        }
        for feature in features
    ]

    return {
        "image_usable": image_usable,
        "image_quality_note": (
            "Simulated test image"
            if image_usable
            else "Image unusable"
        ),
        "observations": observations,
        "general_care": [],
        "needs_clinician_review": False,
        "referral_note": "",
    }


def send_request(payload, token):
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Dev-Token": token,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=15,
        ) as response:
            return (
                response.status,
                json.load(response),
            )

    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"detail": raw}

        return error.code, body


def run_test(name, payload, token, expected):
    print()
    print("=" * 55)
    print("TEST:", name)
    print("=" * 55)

    try:
        status, result = send_request(
            payload,
            token,
        )
    except Exception as error:
        print("FAILED:", type(error).__name__, error)
        return False

    print("HTTP Status:", status)

    if status != expected:
        print("FAILED: Unexpected HTTP status")
        print("Response:", result)
        return False

    if status != 200:
        print("PASSED: Request was rejected as expected")
        return True

    print("RAG Status:", result.get("status"))
    print(
        "Matched observations:",
        result.get("matched_observations", []),
    )

    results = result.get("results", [])

    print("Retrieved documents:", len(results))

    for index, item in enumerate(results, start=1):
        print()
        print(f"Result {index}")
        print("Source:", item.get("source"))
        print("Review:", item.get("review_status"))
        print(
            "Excerpt:",
            item.get("excerpt", "")[:250],
        )

    if result.get("development_only") is not True:
        print("FAILED: Missing development-only flag")
        return False

    if (
        result.get("status") == "draft_unreviewed"
        and any(
            item.get("review_status") != "draft_unreviewed"
            for item in results
        )
    ):
        print("FAILED: Incorrect review status")
        return False

    print()
    print("PASSED")
    return True


def main():
    print()
    print("MATEO AI - MEDICINE RAG TESTING")
    print("Mode: LOCAL DEVELOPMENT")
    print("Gemini: DISABLED")
    print()

    token = getpass.getpass(
        "Paste development token: "
    ).strip()

    if len(token) < 32:
        print("Invalid or missing development token.")
        sys.exit(1)

    tests = [
        (
            "Redness + Dryness",
            create_assessment(
                ["redness", "dryness"]
            ),
            token,
            200,
        ),
        (
            "Irritation + Peeling",
            create_assessment(
                ["irritation", "peeling"]
            ),
            token,
            200,
        ),
        (
            "Unusable Image",
            create_assessment(
                ["redness"],
                image_usable=False,
            ),
            token,
            200,
        ),
        (
            "Unsupported Observation",
            create_assessment(
                ["freckles"]
            ),
            token,
            200,
        ),
        (
            "Invalid Token Rejected",
            create_assessment(
                ["redness"]
            ),
            "invalid-test-token",
            401,
        ),
    ]

    passed = 0

    for name, payload, test_token, expected in tests:
        if run_test(
            name,
            payload,
            test_token,
            expected,
        ):
            passed += 1

    print()
    print("=" * 55)
    print("FINAL TEST SUMMARY")
    print("=" * 55)
    print("Total tests:", len(tests))
    print("Passed:", passed)
    print("Failed:", len(tests) - passed)
    print("Gemini requests: 0")
    print("Status: DEVELOPMENT ONLY")

    if passed != len(tests):
        sys.exit(1)


if __name__ == "__main__":
    main()
