# Test Users for RBAC Implementation

## Credentials

### ADMIN: admin@sentinel.local
- **Password:** `Admin123!`
- **Role:** admin
- **Description:** System Administrator - Full access to all features
- **User Hash:** `e320ca61795c3e80`
- **Status:** UPDATED

### MANAGER: manager1@sentinel.local
- **Password:** `Manager123!`
- **Role:** manager
- **Description:** Engineering Manager - Can view team aggregates and consented individual data
- **User Hash:** `6d29a57b5cd2bacc`
- **Status:** UPDATED

### MANAGER: manager2@sentinel.local
- **Password:** `Manager456!`
- **Role:** manager
- **Description:** Product Manager - Can view team aggregates and consented individual data
- **User Hash:** `45624ca70003fe72`
- **Status:** UPDATED

### EMPLOYEE: employee1@sentinel.local
- **Password:** `Employee123!`
- **Role:** employee
- **Description:** Senior Developer - Can view own data, has consented to share with manager
- **User Hash:** `a502da2df0872bb6`
- **Status:** UPDATED
- **Consent:** consent_share_with_manager = TRUE

### EMPLOYEE: employee2@sentinel.local
- **Password:** `Employee456!`
- **Role:** employee
- **Description:** Junior Developer - Can view own data, has NOT consented to share
- **User Hash:** `4ac59660eb25ff00`
- **Status:** UPDATED
- **Consent:** consent_share_with_manager = FALSE

### EMPLOYEE: employee3@sentinel.local
- **Password:** `Employee789!`
- **Role:** employee
- **Description:** Designer - Can view own data, different manager
- **User Hash:** `d3a7dffd8088dea6`
- **Status:** UPDATED
- **Consent:** consent_share_with_manager = FALSE

## Organization Structure

```
Admin: admin@sentinel.local
L- Full system access

Manager 1: manager1@sentinel.local
|- employee1@sentinel.local (CONSENTED)
L- employee2@sentinel.local (NOT consented)

Manager 2: manager2@sentinel.local
L- employee3@sentinel.local (NOT consented)
```

## RBAC Test Scenarios

### 1. EMPLOYEE VIEW (/me):
- Login as employee1@sentinel.local
- Should see: Own risk score, velocity chart, consent toggles
- Should NOT see: Other users' data, team aggregates

### 2. MANAGER VIEW (/team):
- Login as manager1@sentinel.local
- Should see: Team aggregates (anonymized by default)
- Should see: employee1 details (because consented)
- Should NOT see: employee2 details (no consent, not critical)
- Should NOT see: employee3 details (different manager)

### 3. ADMIN VIEW (/admin):
- Login as admin@sentinel.local
- Should see: System health, all audit logs
- Can view any user data (for audit purposes)

### 4. CONSENT FLOW:
- Login as employee2@sentinel.local
- Toggle "Share with manager" ON
- Login as manager1@sentinel.local
- Should now see employee2 details

### 5. 36-HOUR CRITICAL RULE:
- Set employee3 to CRITICAL risk
- Wait (or simulate) 36 hours
- Manager2 should see employee3 details even without consent

## Database Verification

### Migration Applied
- Migration 002: Add RBAC and consent columns to UserIdentity
- All 5 new columns added successfully
- Index on manager_hash created

### Encryption Status
- [PASS] All emails are properly encrypted with Fernet
- Encrypted data starts with "gAAAAAB" (Fernet token signature)
- No plaintext emails found in database

### RBAC Columns Present
- role: character varying
- consent_share_with_manager: boolean
- consent_share_anonymized: boolean
- monitoring_paused_until: timestamp without time zone
- manager_hash: character varying (indexed)

## Permission Matrix Reference

| Action | Employee (Self) | Manager | Admin |
|--------|-----------------|---------|-------|
| View own risk score | YES | NO | NO |
| View own velocity history | YES | NO | NO |
| View team aggregates | NO | YES (anonymized) | YES |
| View individual details | NO | Consent OR Critical 36h+ | YES (audit only) |
| Pause monitoring | YES | NO | NO |
| Delete own data | YES | NO | NO |
| Run simulation | NO | YES | YES |
| Configure thresholds | NO | NO | YES |
