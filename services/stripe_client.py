import os, stripe as _stripe

stripe = _stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "price_1OpqMiHC37WUuGfBOaJYgOQG")
STRIPE_PRICE_PRO_PLUS = os.getenv("STRIPE_PRICE_PRO_PLUS", "price_1Tahe4HC37WUuGfBbJny56U2")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

TIER_PRICE_MAP = {
    "pro": STRIPE_PRICE_PRO,
    "pro_plus": STRIPE_PRICE_PRO_PLUS,
}


def create_checkout_session(user_id: str, email: str, tier: str, success_url: str, cancel_url: str):
    """Create a Stripe Checkout session for the given tier."""
    price_id = TIER_PRICE_MAP.get(tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {tier}")

    session = stripe.checkout.Session.create(
        customer_email=email,
        client_reference_id=user_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user_id, "tier": tier},
        subscription_data={
            "metadata": {"user_id": user_id, "tier": tier}
        },
    )
    return session


def construct_webhook_event(payload: bytes, sig: str):
    """Verify and parse a Stripe webhook event."""
    return stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)


def get_tier_from_price_id(price_id: str) -> str:
    """Map Stripe price ID to tier name."""
    if price_id == STRIPE_PRICE_PRO:
        return "pro"
    if price_id == STRIPE_PRICE_PRO_PLUS:
        return "pro_plus"
    return "free"


def upgrade_subscription(sub_id: str, new_tier: str):
    """
    Upgrade/downgrade an existing Stripe subscription to a new tier.
    Uses proration — Stripe automatically handles credit for unused time.
    """
    price_id = TIER_PRICE_MAP.get(new_tier)
    if not price_id:
        raise ValueError(f"Unknown tier: {new_tier}")

    # Get current subscription items
    sub = stripe.Subscription.retrieve(sub_id)
    current_item_id = sub['items']['data'][0].id
    current_price_id = sub['items']['data'][0]['price']['id']

    # If already on this tier, nothing to do
    if current_price_id == price_id:
        return {"status": "already_on_tier", "tier": new_tier}

    # Modify the subscription item to the new price
    updated_sub = stripe.Subscription.modify(
        sub_id,
        items=[{
            'id': current_item_id,
            'price': price_id,
        }],
        proration_behavior='create_prorations',
    )
    return {"status": "upgraded", "tier": new_tier, "subscription_id": updated_sub.id}