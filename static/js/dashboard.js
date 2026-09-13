/* NexusOps Dashboard */
const Dashboard = {
    API: '/api/v1',

    statusBadge(s) {
        const m = {
            'SUCCESS':'success', 'RUNNING':'info', 'QUEUED':'warning',
            'FAILED':'danger', 'PERMANENTLY_FAILED':'danger',
            'TIMED_OUT':'danger', 'CANCELLED':'muted',
            'ACTIVE':'success', 'DEAD':'danger', 'INACTIVE':'muted',
            'CRITICAL':'danger', 'HIGH':'warning', 'MEDIUM':'info', 'LOW':'muted'
        };
        return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
    },

    timeAgo(iso) {
        if (!iso) return '--';
        const d = (Date.now() - new Date(iso).getTime()) / 1000;
        if (d < 60) return `${Math.floor(d)}s ago`;
        if (d < 3600) return `${Math.floor(d/60)}m ago`;
        if (d < 86400) return `${Math.floor(d/3600)}h ago`;
        return `${Math.floor(d/86400)}d ago`;
    },

    formatDuration(ms) {
        if (ms === null || ms === undefined) return '--';
        if (ms < 1000) return `${ms}ms`;
        return `${(ms / 1000).toFixed(1)}s`;
    },

    async fetchJSON(url) {
        return (await fetch(url)).json();
    },

    async refresh() {
        try {
            const [dash, runs, workers, attempts] = await Promise.all([
                this.fetchJSON(`${this.API}/dashboard/`),
                this.fetchJSON(`${this.API}/runs/?limit=10`),
                this.fetchJSON(`${this.API}/workers/`),
                this.fetchJSON(`${this.API}/attempts/?status=RUNNING&limit=20`),
            ]);

            document.getElementById('success-rate').textContent = `${dash.runs_24h.success_rate}%`;
            document.getElementById('success-detail').textContent = `${dash.runs_24h.success} / ${dash.runs_24h.total}`;
            document.getElementById('total-runs').textContent = dash.runs_24h.total;
            document.getElementById('runs-detail').textContent = `${dash.runs_7d.total} in 7d`;
            document.getElementById('queue-depth').textContent = dash.queue.queued_runs + dash.queue.running_attempts;
            document.getElementById('queue-detail').textContent = `${dash.queue.queued_runs} runs queued`;
            document.getElementById('active-workers').textContent = dash.workers.active;
            document.getElementById('worker-detail').textContent = `${dash.workers.dead} dead`;
            document.getElementById('total-jobs').textContent = dash.jobs.total;
            document.getElementById('jobs-detail').textContent = `${dash.jobs.active} active`;
            document.getElementById('avg-duration').textContent = this.formatDuration(dash.attempts_24h.avg_duration_ms);

            document.getElementById('runs-table').innerHTML = runs.length === 0
                ? '<tr><td colspan="3" class="empty-state">No runs yet</td></tr>'
                : runs.map(r => `<tr><td>${r.job_name}</td><td>${this.statusBadge(r.status)}</td><td>${this.timeAgo(r.scheduled_for)}</td></tr>`).join('');

            document.getElementById('workers-table').innerHTML = workers.length === 0
                ? '<tr><td colspan="3" class="empty-state">No workers</td></tr>'
                : workers.map(w => `<tr><td>${w.id}</td><td>${this.statusBadge(w.status)}</td><td>${this.timeAgo(w.last_heartbeat_at)}</td></tr>`).join('');

            document.getElementById('attempts-count').textContent = attempts.length;
            document.getElementById('attempts-table').innerHTML = attempts.length === 0
                ? '<tr><td colspan="6" class="empty-state">No running attempts</td></tr>'
                : attempts.map(a => `<tr><td>${a.id.substring(0,8)}...</td><td>${a.job_name}</td><td>${a.worker_id||'--'}</td><td>${this.statusBadge(a.priority)}</td><td>${a.fencing_token}</td><td>${this.statusBadge(a.status)}</td></tr>`).join('');
        } catch (e) { console.error('Dashboard refresh failed:', e); }
    },

    init() {
        this.refresh();
        setInterval(() => this.refresh(), 3000);
    }
};
