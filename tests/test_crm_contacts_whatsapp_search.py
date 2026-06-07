from app.models.person import ChannelType, Person, PersonChannel
from app.services.crm.contacts.service import contacts


def test_list_whatsapp_contacts_dedupes_by_person_id_with_json_metadata(db_session):
    person = Person(
        first_name="Nura",
        last_name="Customer",
        display_name="Nura Customer",
        email="nura.customer@example.test",
        phone="+2348012345678",
        metadata_={"splynx_id": "SPX-123", "source": "test"},
    )
    db_session.add(person)
    db_session.flush()
    db_session.add_all(
        [
            PersonChannel(
                person_id=person.id,
                channel_type=ChannelType.whatsapp,
                address="+2348012345678",
                is_primary=True,
            ),
            PersonChannel(
                person_id=person.id,
                channel_type=ChannelType.phone,
                address="+2348012345678",
            ),
        ]
    )
    db_session.commit()

    results = contacts.list_whatsapp_contacts(db_session, search="nura")

    assert len(results) == 1
    assert results[0]["id"] == str(person.id)
    assert results[0]["whatsapp_address"] == "+2348012345678"
