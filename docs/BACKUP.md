# Database backup and restore

Nightly logical backup (cron on the host, or your platform's scheduler):

    pg_dump --format=custom --file=/backups/bloodlink-$(date +%F).dump "$DATABASE_URL"
    find /backups -name 'bloodlink-*.dump' -mtime +30 -delete

Copy the dumps off the server (object storage or another host); a backup on the
same disk is not a backup. If your host offers managed point-in-time recovery,
turn it on as well.

Restore into an empty database:

    pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" bloodlink-YYYY-MM-DD.dump

**Test a restore at least once a month** on a scratch database, and again before
any risky migration. The data includes health-related personal information:
encrypt backups at rest and restrict who can read them.
