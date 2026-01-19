class SQLiteStateStore:
    def __init__(self, db_name):
        self.connection = sqlite3.connect(db_name)
        self.create_schema()

    def create_schema(self):
        cursor = self.connection.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY,
            repo TEXT,
            issue_number INTEGER,
            issue_id TEXT,
            issue_url TEXT,
            title TEXT,
            author_login TEXT,
            state TEXT,
            created_at DATETIME
        , UNIQUE(repo, issue_number))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS issue_analysis (
            id INTEGER PRIMARY KEY,
            issue_row_id INTEGER,
            analysis TEXT,
            model_info TEXT,
            FOREIGN KEY(issue_row_id) REFERENCES issues(id)
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY,
            issue_row_id INTEGER,
            analysis_id INTEGER,
            channel TEXT,
            status TEXT,
            error TEXT,
            provider_response TEXT,
            FOREIGN KEY(issue_row_id) REFERENCES issues(id),
            FOREIGN KEY(analysis_id) REFERENCES issue_analysis(id)
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS run_log (
            id INTEGER PRIMARY KEY,
            run_time DATETIME
        )''')
        self.connection.commit()

    def has_issue(self, repo, issue_number):
        cursor = self.connection.cursor()
        cursor.execute('SELECT COUNT(1) FROM issues WHERE repo = ? AND issue_number = ?', (repo, issue_number))
        return cursor.fetchone()[0] > 0

    def upsert_issue(self, repo, issue_number, issue_id, issue_url, title, author_login, state, created_at):
        cursor = self.connection.cursor()
        cursor.execute('''INSERT INTO issues (repo, issue_number, issue_id, issue_url, title, author_login, state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(repo, issue_number) DO UPDATE SET issue_id=excluded.issue_id, issue_url=excluded.issue_url, title=excluded.title, author_login=excluded.author_login, state=excluded.state, created_at=excluded.created_at''',
                      (repo, issue_number, issue_id, issue_url, title, author_login, state, created_at))
        self.connection.commit()
        return cursor.lastrowid

    def insert_issue_analysis(self, issue_row_id, analysis, model_info):
        cursor = self.connection.cursor()
        cursor.execute('INSERT INTO issue_analysis (issue_row_id, analysis, model_info) VALUES (?, ?, ?)', (issue_row_id, analysis, model_info))
        self.connection.commit()
        return cursor.lastrowid

    def insert_notification(self, issue_row_id, analysis_id, channel, status, error, provider_response):
        cursor = self.connection.cursor()
        cursor.execute('INSERT INTO notifications (issue_row_id, analysis_id, channel, status, error, provider_response) VALUES (?, ?, ?, ?, ?, ?)', (issue_row_id, analysis_id, channel, status, error, provider_response))
        self.connection.commit()
        return cursor.lastrowid

    def log_run(self):
        cursor = self.connection.cursor()
        cursor.execute('INSERT INTO run_log (run_time) VALUES (?)', [datetime.utcnow()])
        self.connection.commit()

    def list_issues(self):
        pass

    def get_issue(self, issue_row_id):
        pass

    def list_issue_analyses(self, issue_row_id):
        pass

    def get_analysis(self, analysis_id):
        pass

    def list_notifications(self, issue_row_id):
        pass

    def get_notification(self, notification_id):
        pass

    def list_runs(self):
        pass
