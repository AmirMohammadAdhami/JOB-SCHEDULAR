/* NexusOps Runs History */
const Runs = {
    API: '/api/v1',

    statusBadge(s) {
        const m = {
            'SUCCESS':'success', 'RUNNING':'info', 'QUEUED':'warning',
            'FAILED':'danger', 'PERMANENTLY_FAILED':'danger',
            'TIMED_OUT':'danger', 'CANCELLED':'muted',
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
        if (ms < 60000) return `${(ms/1000).toFixed(1)}s`;
        return `${Math.floor(ms/60000)}m ${Math.floor((ms%60000)/1000)}s`;
    },

    timeFormat(iso) {
        if (!iso) return '--';
        return new Date(iso).toLocaleString();
    },

    async loadRuns() {
        const status = document.getElementById('status-filter').value;
        const url = status ? `${this.API}/runs/?status=${status}&limit=50` : `${this.API}/runs/?limit=50`;
        const runs = await (await fetch(url)).json();
        const tbody = document.getElementById('runs-table');
        if (runs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No runs found</td></tr>';
            return;
        }
        tbody.innerHTML = runs.map(r => {
            const dur = r.finished_at && r.started_at
                ? new Date(r.finished_at) - new Date(r.started_at) : null;
            return `
                <tr>
                    <td><code style="font-size:11px">${r.id.substring(0,8)}...</code></td>
                    <td>${r.job_name}</td>
                    <td>${this.statusBadge(r.status)}</td>
                    <td>${this.timeAgo(r.scheduled_for)}</td>
                    <td>${dur ? this.formatDuration(dur) : '--'}</td>
                    <td>${r.total_attempts}</td>
                    <td><button class="btn btn-sm" onclick="Runs.viewRun('${r.id}')">Details</button></td>
                </tr>
            `;
        }).join('');
    },

    async viewRun(id) {
        const run = await (await fetch(`${this.API}/runs/${id}/`)).json();
        const attempts = run.attempts || [];
        let html = `
            <div style="margin-bottom:16px">
                <div style="display:flex;gap:12px;align-items:center;margin-bottom:8px">
                    <strong>${run.job_name}</strong>
                    ${this.statusBadge(run.status)}
                </div>
                <div style="font-size:12px;color:#8b949e">
                    Scheduled: ${this.timeFormat(run.scheduled_for)} |
                    Started: ${this.timeFormat(run.started_at)} |
                    Finished: ${this.timeFormat(run.finished_at)}
                </div>
            </div>
            <h4 style="font-size:13px;margin-bottom:8px">Attempts (${attempts.length})</h4>
        `;
        if (attempts.length === 0) {
            html += '<div class="empty-state">No attempts</div>';
        } else {
            html += `<table><thead><tr>
                <th>#</th><th>Status</th><th>Worker</th><th>Token</th>
                <th>Duration</th><th>Error</th>
            </tr></thead><tbody>`;
            for (const a of attempts) {
                html += `<tr>
                    <td>${a.attempt_number}</td>
                    <td>${this.statusBadge(a.status)}</td>
                    <td>${a.worker_id || '--'}</td>
                    <td>${a.fencing_token}</td>
                    <td>${this.formatDuration(a.duration_ms)}</td>
                    <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                        title="${a.error_message||''}">${a.error_message || '--'}</td>
                </tr>`;
            }
            html += '</tbody></table>';
        }
        document.getElementById('run-detail-content').innerHTML = html;
        document.getElementById('detail-modal').classList.add('active');
    },

    closeDetailModal() {
        document.getElementById('detail-modal').classList.remove('active');
    },

    init() {
        this.loadRuns();
    }
};
