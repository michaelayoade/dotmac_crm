"""Seed CRM inbox with test data for development/testing."""

import argparse
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from app.db import SessionLocal
from app.models.person import Person, PersonChannel, ChannelType as PersonChannelType
from app.models.crm.conversation import Conversation, ConversationTag, Message
from app.models.crm.enums import ChannelType as CrmChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models import Organization


def parse_args():
    parser = argparse.ArgumentParser(description="Seed CRM inbox with test data.")
    parser.add_argument("--clear", action="store_true", help="Clear existing CRM data first")
    return parser.parse_args()


def clear_crm_data(db):
    """Clear existing CRM data."""
    print("Clearing existing CRM data...")
    db.query(Message).delete()
    db.query(ConversationTag).delete()
    db.query(Conversation).delete()
    db.commit()
    print("CRM data cleared.")


def create_organizations(db):
    """Create test organizations."""
    orgs_data = [
        {"name": "Acme Corporation", "email": "contact@acme.co"},
        {"name": "TechStart Inc", "email": "hello@techstart.io"},
        {"name": "GlobalTech Industries", "email": "info@globaltech.com"},
        {"name": "Innovate Dev", "email": "team@innovate.dev"},
        {"name": "Enterprise Solutions", "email": "support@enterprise.org"},
    ]

    orgs = []
    for data in orgs_data:
        org = db.query(Organization).filter(Organization.name == data["name"]).first()
        if not org:
            org = Organization(**data)
            db.add(org)
            db.commit()
            db.refresh(org)
        orgs.append(org)

    return orgs


def create_contacts_and_conversations(db, orgs):
    """Create test contacts, channels, conversations, and messages."""
    now = datetime.now(timezone.utc)

    contacts_data = [
        {
            "display_name": "Sarah Mitchell",
            "email": "sarah.mitchell@acme.co",
            "phone": "+1 555-0123",
            "org_idx": 0,
            "channels": [
                {"type": CrmChannelType.email, "address": "sarah.mitchell@acme.co", "primary": True},
                {"type": CrmChannelType.whatsapp, "address": "+15550123", "primary": False},
            ],
            "conversations": [
                {
                    "status": ConversationStatus.open,
                    "subject": "Fiber installation query",
                    "tags": ["Installation", "Fiber"],
                    "messages": [
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Hi, I wanted to follow up on my fiber installation scheduled for next week. Can you confirm the date and time?",
                            "time_offset": timedelta(hours=-4),
                        },
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.email,
                            "body": "Hello Sarah! Thank you for reaching out. Let me check our system for your installation details.\n\nI can see your installation is scheduled for January 15th between 9 AM and 12 PM. A technician will call you 30 minutes before arrival.",
                            "time_offset": timedelta(hours=-3, minutes=-45),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Perfect, that works for me. Will I need to be present the entire time?",
                            "time_offset": timedelta(hours=-2),
                        },
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.email,
                            "body": "Yes, we require someone 18+ to be present during the installation to sign off on the work and receive equipment instructions. The installation typically takes 2-3 hours depending on your setup.",
                            "time_offset": timedelta(hours=-1, minutes=-30),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Understood. One more question - what speed tier will I be getting? I upgraded my plan last month.",
                            "time_offset": timedelta(minutes=-15),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Also, will the technician bring the router or do I need to pick it up somewhere?",
                            "time_offset": timedelta(minutes=-5),
                        },
                    ],
                },
            ],
        },
        {
            "display_name": "Marcus Chen",
            "email": "m.chen@techstart.io",
            "phone": "+1 555-0456",
            "org_idx": 1,
            "channels": [
                {"type": CrmChannelType.email, "address": "m.chen@techstart.io", "primary": False},
                {"type": CrmChannelType.whatsapp, "address": "+15550456", "primary": True},
            ],
            "conversations": [
                {
                    "status": ConversationStatus.open,
                    "subject": None,
                    "tags": ["Support"],
                    "messages": [
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "Hey, my internet has been really slow since yesterday. Can someone check?",
                            "time_offset": timedelta(hours=-2),
                        },
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "Hi Marcus! Sorry to hear about the slow speeds. I'm checking your connection now. Can you tell me - is it slow on all devices or just one?",
                            "time_offset": timedelta(hours=-1, minutes=-45),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "All devices. Both my laptop and phone are affected.",
                            "time_offset": timedelta(hours=-1, minutes=-30),
                        },
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "I see there was some maintenance in your area yesterday. Let me run a diagnostic on your line. This will take about 2 minutes.",
                            "time_offset": timedelta(hours=-1),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "Thanks for the quick response! When can the technician arrive?",
                            "time_offset": timedelta(minutes=-23),
                        },
                    ],
                },
            ],
        },
        {
            "display_name": "Elena Rodriguez",
            "email": "elena.r@globaltech.com",
            "phone": "+1 555-0789",
            "org_idx": 2,
            "channels": [
                {"type": CrmChannelType.email, "address": "elena.r@globaltech.com", "primary": True},
            ],
            "conversations": [
                {
                    "status": ConversationStatus.pending,
                    "subject": "Billing discrepancy - Invoice #4521",
                    "tags": ["Billing", "Enterprise"],
                    "messages": [
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Hello,\n\nI noticed there's a charge on my latest invoice (#4521) that I don't recognize. The line item says 'Premium Support Add-on' for $49.99, but I never subscribed to this service.\n\nCould you please look into this and issue a credit if this was charged in error?\n\nThank you,\nElena Rodriguez\nGlobalTech Industries",
                            "time_offset": timedelta(hours=-3),
                        },
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.email,
                            "body": "Hi Elena,\n\nThank you for bringing this to our attention. I apologize for any confusion.\n\nI've looked into your account and I can see the Premium Support Add-on was added during your last plan upgrade. However, if you didn't intend to add this service, I'd be happy to remove it and issue a credit for the charge.\n\nCould you confirm you'd like me to proceed with the refund?\n\nBest regards,\nSupport Team",
                            "time_offset": timedelta(hours=-2, minutes=-30),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Yes, please remove it and issue the credit. I definitely didn't mean to add that.\n\nAlso, can you send me an updated invoice once the credit is applied?\n\nThanks,\nElena",
                            "time_offset": timedelta(hours=-2),
                        },
                    ],
                },
            ],
        },
        {
            "display_name": "David Park",
            "email": "d.park@innovate.dev",
            "phone": "+1 555-0321",
            "org_idx": 3,
            "channels": [
                {"type": CrmChannelType.email, "address": "d.park@innovate.dev", "primary": False},
                {"type": CrmChannelType.whatsapp, "address": "+15550321", "primary": True},
            ],
            "conversations": [
                {
                    "status": ConversationStatus.snoozed,
                    "subject": None,
                    "tags": ["Sales", "Upgrade"],
                    "messages": [
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "Hi David! I noticed you've been on the Business 100 plan for a while. We have a new Business 500 plan that might be a better fit for your team. Would you like to hear more about it?",
                            "time_offset": timedelta(days=-1),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "Hey! Yeah, we've actually been thinking about upgrading. What's the price difference?",
                            "time_offset": timedelta(hours=-20),
                        },
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "Great timing! The Business 500 is $79/month compared to your current $49/month. You get 5x the speed and priority support. I can also offer you the first 3 months at your current rate as a loyalty discount.",
                            "time_offset": timedelta(hours=-18),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "That sounds interesting. Let me check with my team and get back to you tomorrow.",
                            "time_offset": timedelta(hours=-6),
                        },
                    ],
                },
            ],
        },
        {
            "display_name": "Amanda Foster",
            "email": "a.foster@enterprise.org",
            "phone": "+1 555-0654",
            "org_idx": 4,
            "channels": [
                {"type": CrmChannelType.email, "address": "a.foster@enterprise.org", "primary": True},
            ],
            "conversations": [
                {
                    "status": ConversationStatus.resolved,
                    "subject": "Service upgrade completed",
                    "tags": ["Upgrade", "VIP"],
                    "messages": [
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Hi,\n\nI wanted to confirm that our service upgrade to the Enterprise plan was completed successfully. Can you verify that all the new features are active?\n\nThanks,\nAmanda",
                            "time_offset": timedelta(days=-2),
                        },
                        {
                            "direction": MessageDirection.outbound,
                            "channel": CrmChannelType.email,
                            "body": "Hi Amanda,\n\nYes, I can confirm your upgrade to Enterprise plan was completed yesterday at 3:00 PM. All features are now active:\n\n- Dedicated IP address: 203.0.113.50\n- Priority support queue\n- 99.9% SLA guarantee\n- Unlimited bandwidth\n\nPlease let me know if you have any questions!\n\nBest,\nSupport Team",
                            "time_offset": timedelta(days=-2, hours=2),
                        },
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Everything is working perfectly now. Thank you for the excellent support!\n\nAmanda",
                            "time_offset": timedelta(days=-1),
                        },
                    ],
                },
            ],
        },
        {
            "display_name": "James Wilson",
            "email": "j.wilson@startup.co",
            "phone": "+1 555-0987",
            "org_idx": None,
            "channels": [
                {"type": CrmChannelType.email, "address": "j.wilson@startup.co", "primary": True},
                {"type": CrmChannelType.whatsapp, "address": "+15550987", "primary": False},
            ],
            "conversations": [
                {
                    "status": ConversationStatus.open,
                    "subject": "New service inquiry",
                    "tags": ["Sales", "New Customer"],
                    "messages": [
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.email,
                            "body": "Hello,\n\nI'm interested in setting up internet service for our new office location at 123 Tech Park Drive. We're a team of 25 and need reliable, high-speed connectivity.\n\nCan you tell me what options are available in that area?\n\nThanks,\nJames Wilson",
                            "time_offset": timedelta(hours=-1),
                        },
                    ],
                },
            ],
        },
        {
            "display_name": "Lisa Thompson",
            "email": "lisa.t@designstudio.com",
            "phone": "+1 555-0741",
            "org_idx": None,
            "channels": [
                {"type": CrmChannelType.whatsapp, "address": "+15550741", "primary": True},
            ],
            "conversations": [
                {
                    "status": ConversationStatus.open,
                    "subject": None,
                    "tags": ["Support", "Outage"],
                    "messages": [
                        {
                            "direction": MessageDirection.inbound,
                            "channel": CrmChannelType.whatsapp,
                            "body": "Hi! Is there an outage in the downtown area? My internet just went out about 10 minutes ago.",
                            "time_offset": timedelta(minutes=-10),
                        },
                    ],
                },
            ],
        },
    ]

    created_count = {"contacts": 0, "channels": 0, "conversations": 0, "messages": 0, "tags": 0}

    for contact_data in contacts_data:
        # Create person
        display_name = contact_data["display_name"]
        if display_name and " " in display_name.strip():
            first_name, last_name = display_name.strip().split(" ", 1)
        else:
            first_name = display_name or "Contact"
            last_name = "Contact"
        contact = Person(
            first_name=first_name,
            last_name=last_name,
            display_name=display_name,
            email=contact_data["email"],
            phone=contact_data["phone"],
            organization_id=orgs[contact_data["org_idx"]].id if contact_data["org_idx"] is not None else None,
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        created_count["contacts"] += 1

        # Create channels
        channel_map = {}
        for ch_data in contact_data["channels"]:
            channel = PersonChannel(
                person_id=contact.id,
                channel_type=PersonChannelType(ch_data["type"].value),
                address=ch_data["address"],
                is_primary=ch_data["primary"],
            )
            db.add(channel)
            db.commit()
            db.refresh(channel)
            channel_map[ch_data["type"]] = channel
            created_count["channels"] += 1

        # Create conversations
        for conv_data in contact_data["conversations"]:
            conversation = Conversation(
                person_id=contact.id,
                status=conv_data["status"],
                subject=conv_data["subject"],
            )
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
            created_count["conversations"] += 1

            # Create tags
            for tag_name in conv_data.get("tags", []):
                tag = ConversationTag(
                    conversation_id=conversation.id,
                    tag=tag_name,
                )
                db.add(tag)
                created_count["tags"] += 1

            # Create messages
            last_message_at = None
            for msg_data in conv_data["messages"]:
                msg_time = now + msg_data["time_offset"]
                channel = channel_map.get(msg_data["channel"])

                message = Message(
                    conversation_id=conversation.id,
                    person_channel_id=channel.id if channel else None,
                    channel_type=msg_data["channel"],
                    direction=msg_data["direction"],
                    status=MessageStatus.received if msg_data["direction"] == MessageDirection.inbound else MessageStatus.sent,
                    body=msg_data["body"],
                    received_at=msg_time if msg_data["direction"] == MessageDirection.inbound else None,
                    sent_at=msg_time if msg_data["direction"] == MessageDirection.outbound else None,
                )
                db.add(message)
                last_message_at = msg_time
                created_count["messages"] += 1

            # Update conversation last_message_at
            conversation.last_message_at = last_message_at
            db.commit()

    return created_count


def main():
    load_dotenv()
    args = parse_args()
    db = SessionLocal()

    try:
        if args.clear:
            clear_crm_data(db)

        print("Creating test organizations...")
        orgs = create_organizations(db)
        print(f"  Created/found {len(orgs)} organizations")

        print("Creating contacts, conversations, and messages...")
        counts = create_contacts_and_conversations(db, orgs)
        print(f"  Created {counts['contacts']} contacts")
        print(f"  Created {counts['channels']} contact channels")
        print(f"  Created {counts['conversations']} conversations")
        print(f"  Created {counts['messages']} messages")
        print(f"  Created {counts['tags']} tags")

        print("\nCRM inbox seed data created successfully!")
        print("Access the inbox at: /admin/crm/inbox")

    finally:
        db.close()


if __name__ == "__main__":
    main()
