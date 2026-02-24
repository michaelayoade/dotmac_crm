from app.services.crm.inbox import outbound


def test_append_attachment_links_to_body_with_existing_text():
    result = outbound._append_attachment_links_to_body(
        "Hello",
        ["https://crm.dotmac.io/public/media/messages/a.pdf"],
    )
    assert result == "Hello\n\nAttachments:\nhttps://crm.dotmac.io/public/media/messages/a.pdf"


def test_append_attachment_links_to_body_without_text():
    result = outbound._append_attachment_links_to_body(
        "",
        ["https://crm.dotmac.io/public/media/messages/a.pdf"],
    )
    assert result == "Attachments:\nhttps://crm.dotmac.io/public/media/messages/a.pdf"
