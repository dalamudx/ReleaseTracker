import pytest


@pytest.mark.asyncio
async def test_notifier_language_defaults_to_english(storage):
    notifier = await storage.create_notifier(
        {
            "name": "default-language-webhook",
            "type": "webhook",
            "url": "https://example.com/webhook",
            "events": ["new_release"],
            "enabled": True,
        }
    )

    assert notifier.language == "en"


@pytest.mark.asyncio
async def test_wecom_notifier_type_persists(storage):
    notifier = await storage.create_notifier(
        {
            "name": "enterprise-wechat",
            "type": "wecom",
            "url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=redacted",
            "events": ["new_release", "republish"],
            "enabled": True,
            "language": "zh",
        }
    )

    assert notifier.type == "wecom"
    assert (await storage.get_notifier(notifier.id)).type == "wecom"


@pytest.mark.asyncio
async def test_notifiers_search_visible_metadata(storage):
    await storage.create_notifier(
        {
            "name": "ops-webhook",
            "type": "webhook",
            "url": "https://ops.example.com/releases",
            "events": ["new_release"],
            "enabled": True,
            "description": "Operations release notifications",
        }
    )
    await storage.create_notifier(
        {
            "name": "marketing-webhook",
            "type": "webhook",
            "url": "https://marketing.example.com/releases",
            "events": ["new_release"],
            "enabled": True,
        }
    )

    notifiers = await storage.get_notifiers_paginated(search="OPERATIONS")

    assert await storage.get_total_notifiers_count("OPERATIONS") == 1
    assert [notifier.name for notifier in notifiers] == ["ops-webhook"]


@pytest.mark.asyncio
async def test_notifier_language_persists_on_create_and_update(storage):
    notifier = await storage.create_notifier(
        {
            "name": "localized-webhook",
            "type": "webhook",
            "url": "https://example.com/webhook",
            "events": ["new_release"],
            "enabled": True,
            "language": "zh",
        }
    )

    assert notifier.language == "zh"

    updated = await storage.update_notifier(notifier.id, {"language": "en"})

    assert updated.language == "en"
