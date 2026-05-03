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
