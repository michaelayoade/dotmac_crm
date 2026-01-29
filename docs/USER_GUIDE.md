# DotMac Omni User Guide

A comprehensive guide for using the DotMac Omni-Channel Field Service and CRM platform.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Admin Portal](#admin-portal)
3. [Customer Portal](#customer-portal)
4. [Reseller Portal](#reseller-portal)
5. [Vendor Portal](#vendor-portal)
6. [Common Tasks & Workflows](#common-tasks--workflows)
7. [Tips & Shortcuts](#tips--shortcuts)

---

## Getting Started

### System Requirements

- Modern web browser (Chrome, Firefox, Safari, Edge)
- Stable internet connection
- Screen resolution: 1280x720 minimum (1920x1080 recommended)

### Accessing the System

| Portal | URL | Who Uses It |
|--------|-----|-------------|
| Admin Portal | `https://your-domain.com/admin` | Staff & Administrators |
| Customer Portal | `https://your-domain.com/portal` | End Customers |
| Reseller Portal | `https://your-domain.com/reseller` | Partner Resellers |
| Vendor Portal | `https://your-domain.com/vendor` | Installation Contractors |

### Logging In

1. Navigate to your portal URL
2. Enter your **username** or **email address**
3. Enter your **password**
4. Click **Sign In**
5. If MFA is enabled, enter the verification code from your authenticator app

### Navigation Basics

- **Sidebar** (Admin): Click menu items to navigate; click section headers to expand/collapse
- **Top Navigation** (Other Portals): Click menu items in the horizontal navbar
- **Dark Mode**: Click the moon/sun icon in the header to toggle
- **User Menu**: Click your avatar/initials in the top-right corner for profile options

---

## Admin Portal

The Admin Portal is the central hub for managing all aspects of your field service operations.

### Dashboard Overview

The dashboard provides a real-time snapshot of your operations:

| Section | Description |
|---------|-------------|
| **Network Health** | OLT/ONT status, active alarms, and connectivity metrics |
| **Service Orders** | Pipeline view of pending, in-progress, and completed orders |
| **Key Metrics** | Active customers, open tickets |
| **Recent Activity** | Live feed of system events |
| **Today's Dispatch** | Field technician assignments and status |

### Sidebar Navigation

The sidebar is organized into logical sections:

#### Customers Section

**Customers**
- View all customer accounts (individuals and organizations)
- Search and filter by name, account number, status
- Create new customer accounts
- View customer details and history

#### Inventory

- Track physical equipment (modems, routers, ONTs)
- Manage stock levels
- Assign equipment to service orders

#### Network Section

**Network Map**
- Interactive GIS map showing network infrastructure
- View POP sites, fiber routes, and customer locations
- Click markers for details

**POP Sites**
- Manage Point of Presence locations
- View site details and equipment
- Track site status

**Core Network**

| Feature | Description |
|---------|-------------|
| All Core Devices | Complete list of core infrastructure |
| Core Routers | Border and core routing equipment |
| Distribution Switches | Distribution layer switches |
| Access Switches | Access layer equipment |
| Aggregation Devices | Traffic aggregation equipment |

**GPON Infrastructure**

| Feature | Description |
|---------|-------------|
| OLTs | Optical Line Terminals - manage head-end equipment |
| ONTs / CPE | Customer premise equipment management |
| All PON Devices | Complete PON device inventory |

**Fiber Plant / ODN**

| Feature | Description |
|---------|-------------|
| Fiber Map | Visual fiber route mapping |
| FDH Cabinets | Fiber Distribution Hub management |
| Splitters | Optical splitter tracking |
| Fiber Strands | Individual strand management |
| Splice Closures | Splice point documentation |
| Fiber Reports | Fiber plant analytics |

**IP / VLAN Management**

| Feature | Description |
|---------|-------------|
| IP Pools & Blocks | Manage IP address allocation |
| VLANs | Virtual LAN configuration |

#### Operations Section

**Service Orders**
- Create new service orders (new installs, upgrades, disconnects)
- Track order progress through workflow stages
- Assign to technicians or vendors

**Installations**
- Schedule installation appointments
- Track installation progress
- Document completion with photos

**Field Service / Work Orders**
- Create maintenance and repair work orders
- Assign to field technicians
- Track work order lifecycle

**Dispatch Board**
- Visual calendar of technician schedules
- Drag-and-drop assignment
- Real-time status updates

**Trouble Tickets**
- Customer support ticket management
- Assign to support agents
- Track resolution time
- Categorize by issue type

**CRM Inbox**
- Omni-channel messaging (email, SMS, chat)
- Customer conversation history
- Quick response templates

**Projects**
- Large-scale project management
- Multi-phase installation projects
- Vendor assignment and tracking

#### Integrations Section

**Connectors**
- Configure external system connections
- API credentials management
- Connection status monitoring

**Integration Targets**
- Define integration endpoints
- Map data fields
- Configure sync schedules

**Jobs**
- View scheduled integration jobs
- Manual job execution
- Job history and logs

**Webhooks**
- Configure outbound webhooks
- Event triggers
- Delivery monitoring

#### System Section

**Users**
- Create and manage user accounts
- Assign roles and permissions
- Reset passwords
- Enable/disable accounts

**Roles & Permissions**
- Define user roles
- Configure granular permissions
- Role-based access control

**API Keys**
- Generate API keys for integrations
- Set key permissions
- Track API usage

**Audit Log**
- View system activity history
- Filter by user, action, date
- Export audit data

**Tasks & Scheduler**
- View scheduled system tasks
- Configure task schedules
- Monitor task execution

**Legal Documents**
- Manage terms of service
- Privacy policy management
- Document versioning

**Settings**
- System configuration
- Company information
- Default values and preferences

---

## Customer Portal

The Customer Portal allows customers to view their services and get support.

### Dashboard

The customer dashboard shows:

| Widget | Description |
|--------|-------------|
| **Service Status** | Active or Suspended indicator |
| **Open Tickets** | Number of pending support requests |
| **Quick Actions** | Shortcuts to common tasks |
| **Recent Activity** | Timeline of recent account events |

### Quick Actions

- **Report an Issue** - Create a support ticket
- **Update Profile** - Change contact information

### Support

**Create a Ticket**
1. Navigate to **Support** > **New Ticket**
2. Select issue category:
   - Technical Issue
   - Service Request
   - General Inquiry
3. Describe your issue in detail
4. Attach screenshots if helpful
5. Submit ticket

**Track Tickets**
- View all your tickets
- Check status (Open, In Progress, Resolved)
- Add comments to existing tickets
- Receive email updates

### Installations

- View scheduled installation appointments
- Check installation status
- Reschedule if needed
- View installation history

### Service Orders

- Track new service requests
- View order progress
- Estimated completion dates

### Work Orders

- View scheduled maintenance visits
- Technician assignment details
- Work order status updates

### Profile Settings

**Update Contact Information**
1. Click your avatar > **Profile**
2. Update name, email, phone
3. Save changes

**Change Password**
1. Click your avatar > **Security**
2. Enter current password
3. Enter new password (twice)
4. Save changes

---

## Reseller Portal

The Reseller Portal allows partners to manage their customer accounts.

### Dashboard

| Metric | Description |
|--------|-------------|
| **Total Accounts** | Number of customer accounts under management |

**Recent Accounts Table**
- Customer Name
- Account Number
- Status (Active, Suspended, etc.)
- Actions

### Accounts Management

**View All Accounts**
1. Navigate to **Accounts**
2. See complete list of your customer accounts
3. Search by name or account number
4. Filter by status

**Account Details**
- Click an account to view full details
- Account information
- Support tickets

### View as Customer

This feature allows you to see exactly what your customer sees:

1. Find the customer account
2. Click **View as Customer**
3. You'll be logged into the Customer Portal as that customer
4. A yellow banner shows you're in impersonation mode
5. Click **Stop Impersonation** to return to Reseller Portal

> **Note**: All actions taken while impersonating are logged for audit purposes.

---

## Vendor Portal

The Vendor Portal is for installation contractors and service providers.

### Dashboard

| Widget | Description |
|--------|-------------|
| **Open Bidding** | Number of projects available to bid on |
| **My Projects** | Number of assigned projects |
| **Available Projects** | List of open bidding opportunities |
| **My Projects** | List of your assigned work |

### Available Projects

Browse projects open for bidding:

1. Navigate to **Available Projects**
2. View project details:
   - Project ID
   - Location
   - Scope of work
   - Bidding close date
3. Submit your bid/quote

### My Projects

Manage your assigned projects:

**Project List**
- Project ID and name
- Customer/location
- Status (Pending, In Progress, Completed)
- Due date

**Project Details**
- Full project specifications
- Required materials
- Installation instructions
- Customer contact information

### Quote Builder

Create professional quotes:

1. Navigate to **Quote Builder**
2. Add line items (labor, materials, equipment)
3. Set quantities and prices
4. Add notes or terms
5. Generate quote PDF
6. Submit to DotMac

### As-Built Submission

Document completed installations:

1. Open the completed project
2. Navigate to **As-Built**
3. Upload photos:
   - Equipment installation
   - Cable routing
   - Test results
4. Complete checklist items
5. Submit for approval

---

## Common Tasks & Workflows

### Onboarding a New Customer (Admin)

```
1. Create Customer Account
   Admin > Customers > New Customer
   ↓
2. Create Service Order
   Admin > Operations > Service Orders > New
   Type: New Install
   ↓
3. Schedule Installation
   Assign to technician or vendor
   Set appointment date/time
   ↓
4. Complete Installation
   Technician marks complete
   As-built documentation
   ↓
5. Activate Service
   Provision network (VLAN)
```

### Processing a Support Ticket (Admin)

```
1. Review Ticket
   Admin > Operations > Trouble Tickets
   Open ticket details
   ↓
2. Assess Issue
   Review customer information
   Check network status
   ↓
3. Assign Agent
   Assign to appropriate team member
   Set priority level
   ↓
4. Create Work Order (if needed)
   If field visit required
   Schedule technician
   ↓
5. Resolve Issue
   Document resolution
   Update ticket status
   ↓
6. Close Ticket
   Mark as resolved
   Customer notified automatically
```

### Adding Network Equipment (Admin)

```
1. Create POP Site (if new location)
   Admin > Network > POP Sites > New
   Enter location details
   ↓
2. Add OLT
   Admin > Network > GPON > OLTs > New
   Configure OLT settings
   Assign to POP site
   ↓
3. Add ONT/CPE
   Admin > Network > GPON > ONTs > New
   Enter serial number
   ↓
4. Configure VLAN
   Admin > Network > IP/VLAN > VLANs
   Create or assign VLAN
   ↓
5. Test Connectivity
   Verify ONT registration
   Test customer connection
```

---

## Tips & Shortcuts

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `/` | Focus search box |
| `Esc` | Close modal/dropdown |
| `?` | Show keyboard shortcuts (if enabled) |

### Search Tips

- Use quotes for exact phrases: `"John Smith"`
- Search by account number: `ACC-12345`
- Filter by status: `status:active`

### Dashboard Customization

- Drag widgets to rearrange (if enabled)
- Click refresh icon to update data
- Use date pickers to change time ranges

### Bulk Operations

Many list views support bulk operations:

1. Check the checkbox in the header to select all
2. Or check individual items
3. Use the bulk action dropdown
4. Common bulk actions:
   - Export to CSV
   - Send notifications
   - Update status
   - Delete

### Export Data

Most tables support data export:

1. Navigate to the list view
2. Apply desired filters
3. Click **Export** button
4. Choose format (CSV, Excel, PDF)
5. Download file

### Dark Mode

Toggle dark mode for comfortable viewing:

1. Click the sun/moon icon in the header
2. Or: User menu > Settings > Appearance
3. Preference is saved automatically

### Mobile Access

The portals are mobile-responsive:

- Sidebar collapses to hamburger menu
- Tables become scrollable cards
- Forms adapt to screen size
- Touch-friendly buttons and controls

---

## Getting Help

### In-App Help

- Look for `?` icons next to features for tooltips
- Check the Help section in your user menu

### Support Contacts

- **Technical Support**: support@your-domain.com
- **Phone**: +1 (XXX) XXX-XXXX

### Training Resources

- Video tutorials (if available)
- Knowledge base articles
- Release notes for new features

---

## Glossary

| Term | Definition |
|------|------------|
| **Account** | A customer entity (person or organization) |
| **OLT** | Optical Line Terminal - head-end fiber equipment |
| **ONT** | Optical Network Terminal - customer fiber modem |
| **CPE** | Customer Premise Equipment |
| **GPON** | Gigabit Passive Optical Network |
| **VLAN** | Virtual Local Area Network |
| **POP** | Point of Presence - network location |
| **FDH** | Fiber Distribution Hub |
| **Service Order** | Request for new service or changes |
| **Work Order** | Field service task assignment |

---

*Last Updated: January 2026*
*Version: 2.0*
