/* NexusOps Workers */
const Workers = {
    API: '/api/v1',

    statusBadge(s) {
        const m = {
            'ACTIVE':'success', 'DEAD':'danger',
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

    timeFormat(iso) {
        if (!iso) return '--';
        return new Date(iso).toLocaleString();
    },

    async loadWorkers() {
        const workers = await (await fetch(`${this.API}/workers/`)).json();
        const tbody = document.getElementById('workers-table');
        if (workers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No workers registered</td></tr>';
            return;
        }
        tbody.innerHTML = workers.map(w => `
            <tr class="clickable" onclick="Workers.viewWorker('${w.id}')">
                <td><code>${w.id}</code></td>
                <td>${w.hostname}</td>
                <td>${this.statusBadge(w.status)}</td>
                <td>${w.is_alive ? '<span style="color:#3fb950">Yes</span>' : '<span style="color:#f85149">No</span>'}</td>
                <td>${this.timeFormat(w.started_at)}</td>
                <td>${this.timeAgo(w.last_heartbeat_at)}</td>
                <td>--</td>
            </tr>
        `).join('');
    },

    async viewWorker(id) {
        const w = await (await fetch(`${this.API}/workers/${id}/`)).json();
        const attempts = w.running_attempts || [];
        let html = `
            <div style="margin-bottom:16px">
                <div style="display:flex;gap:12px;align-items:center;margin-bottom:8px">
                    <strong>${w.id}</strong>
                    ${this.statusBadge(w.status)}
                    ${w.is_alive ? '<span style="color:#3fb950;font-size:12px">ALIVE</span>' : '<span style="color:#f85149;font-size:12px">DEAD</span>'}
                </div>
                <div style="font-size:12px;color:#8b949e">
                    Hostname: ${w.hostname} |
                    Started: ${this.timeFormat(w.started_at)} |
                    Last Heartbeat: ${this.timeFormat(w.last_heartbeat_at)}
                </div>
            </div>
            <h4 style="font-size:13px;margin-bottom:8px">Running Attempts (${attempts.length})</h4>
        `;
        if (attempts.length === 0) {
            html += '<div class="empty-state">No running attempts</div>';
        } else {
            html += `<table><thead><tr>
                <th>Attempt</th><th>Job</th><th>Priority</th><th>Token</th><th>Lease Expires</th>
            </tr></thead><tbody>`;
            for (const a of attempts) {
                html += `<tr>
                    <td><code>${a.id.substring(0,8)}...</code></td>
                    <td>${a.job_id.substring(0,8)}...</td>
                    <td>${this.statusBadge(a.priority)}</td>
                    <td>${a.fencing_token}</td>
                    <td>${this.timeFormat(a.lease_expires_at)}</td>
                </tr>`;
            }
            html += '</tbody></table>';
        }
        document.getElementById('worker-detail-content').innerHTML = html;
        document.getElementById('detail-modal').classList.add('active');
    },

    closeDetailModal() {
        document.getElementById('detail-modal').classList.remove('active');
    },

    init() {
        this.loadWorkers();
        setInterval(() => this.loadWorkers(), 3000);
    }
};
