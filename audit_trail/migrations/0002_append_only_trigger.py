from django.db import migrations

CREATE = """
CREATE FUNCTION audit_trail_auditlog_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_trail_auditlog is append-only (% blocked)', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_trail_auditlog_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_trail_auditlog
    FOR EACH ROW EXECUTE FUNCTION audit_trail_auditlog_append_only();

CREATE TRIGGER audit_trail_auditlog_no_truncate
    BEFORE TRUNCATE ON audit_trail_auditlog
    FOR EACH STATEMENT EXECUTE FUNCTION audit_trail_auditlog_append_only();
"""

DROP = """
DROP TRIGGER IF EXISTS audit_trail_auditlog_no_truncate ON audit_trail_auditlog;
DROP TRIGGER IF EXISTS audit_trail_auditlog_no_update_delete ON audit_trail_auditlog;
DROP FUNCTION IF EXISTS audit_trail_auditlog_append_only();
"""


class Migration(migrations.Migration):
    """The database itself refuses to change or remove audit rows (defence beyond the Python model).

    Retention clean-ups must be a deliberate, separate maintenance step (drop the trigger, purge, recreate).
    The function is created in the college's own schema because migrations run with that search_path.
    """

    dependencies = [('audit_trail', '0001_initial')]
    operations = [migrations.RunSQL(CREATE, DROP)]
