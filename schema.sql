CREATE TABLE companies (
	id INTEGER NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	domain VARCHAR(255), 
	website VARCHAR(512), 
	sector VARCHAR(22) NOT NULL, 
	sub_sector VARCHAR(100), 
	tier VARCHAR(7) NOT NULL, 
	tier_confidence FLOAT NOT NULL, 
	tier_reason TEXT, 
	country VARCHAR(64), 
	city VARCHAR(128), 
	region VARCHAR(128), 
	employee_count INTEGER, 
	est_revenue_eur FLOAT, 
	brands VARCHAR(512), 
	description TEXT, 
	notes TEXT, 
	source VARCHAR(64), 
	status VARCHAR(14) NOT NULL, 
	extra JSON, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_company_domain UNIQUE (domain)
);

CREATE INDEX ix_companies_name ON companies (name);

CREATE INDEX ix_companies_tier ON companies (tier);

CREATE INDEX ix_companies_sector ON companies (sector);

CREATE INDEX ix_companies_status ON companies (status);

CREATE TABLE events (
	id INTEGER NOT NULL, 
	entity_type VARCHAR(32) NOT NULL, 
	entity_id INTEGER, 
	kind VARCHAR(64) NOT NULL, 
	payload JSON, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_events_entity_id ON events (entity_id);

CREATE INDEX ix_events_created_at ON events (created_at);

CREATE INDEX ix_events_kind ON events (kind);

CREATE INDEX ix_events_entity_type ON events (entity_type);

CREATE TABLE sequences (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	sector VARCHAR(22) NOT NULL, 
	tier VARCHAR(7) NOT NULL, 
	language VARCHAR(8) NOT NULL, 
	step_delays_days JSON NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE suppressions (
	id INTEGER NOT NULL, 
	value VARCHAR(255) NOT NULL, 
	reason VARCHAR(64) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (value)
);

CREATE TABLE templates (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	sector VARCHAR(22) NOT NULL, 
	tier VARCHAR(7) NOT NULL, 
	step INTEGER NOT NULL, 
	language VARCHAR(8) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_template_slot UNIQUE (sector, tier, step, language)
);

CREATE TABLE contacts (
	id INTEGER NOT NULL, 
	company_id INTEGER NOT NULL, 
	first_name VARCHAR(100), 
	last_name VARCHAR(100), 
	title VARCHAR(255), 
	role VARCHAR(64), 
	seniority_rank INTEGER NOT NULL, 
	email VARCHAR(255), 
	email_status VARCHAR(8) NOT NULL, 
	email_source VARCHAR(64), 
	email_confidence FLOAT NOT NULL, 
	linkedin_url VARCHAR(512), 
	phone VARCHAR(64), 
	language VARCHAR(8) NOT NULL, 
	do_not_contact BOOLEAN NOT NULL, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_contact_email UNIQUE (email), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_contacts_company_id ON contacts (company_id);

CREATE TABLE enrollments (
	id INTEGER NOT NULL, 
	contact_id INTEGER NOT NULL, 
	sequence_id INTEGER NOT NULL, 
	status VARCHAR(12) NOT NULL, 
	current_step INTEGER NOT NULL, 
	next_send_at DATETIME, 
	gmail_thread_id VARCHAR(64), 
	last_message_id_header VARCHAR(255), 
	last_sent_at DATETIME, 
	replied_at DATETIME, 
	stopped_reason VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_enrollment UNIQUE (contact_id, sequence_id), 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE, 
	FOREIGN KEY(sequence_id) REFERENCES sequences (id)
);

CREATE INDEX ix_enrollments_contact_id ON enrollments (contact_id);

CREATE INDEX ix_enrollments_next_send_at ON enrollments (next_send_at);

CREATE INDEX ix_enrollments_status ON enrollments (status);

CREATE INDEX ix_enrollments_sequence_id ON enrollments (sequence_id);

CREATE TABLE messages (
	id INTEGER NOT NULL, 
	enrollment_id INTEGER NOT NULL, 
	contact_id INTEGER NOT NULL, 
	step INTEGER NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	status VARCHAR(8) NOT NULL, 
	scheduled_at DATETIME, 
	sent_at DATETIME, 
	gmail_message_id VARCHAR(64), 
	gmail_thread_id VARCHAR(64), 
	message_id_header VARCHAR(255), 
	error TEXT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(enrollment_id) REFERENCES enrollments (id) ON DELETE CASCADE, 
	FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE CASCADE
);

CREATE INDEX ix_messages_contact_id ON messages (contact_id);

CREATE INDEX ix_messages_enrollment_id ON messages (enrollment_id);

CREATE INDEX ix_messages_status ON messages (status);
