/* NexusOps Jobs Management */
const Jobs = {
    API: '/api/v1/jobs',

    statusBadge(s) {
        const m = { 'ACTIVE':'success', 'INACTIVE':'muted', 'DELETED':'danger' };
        return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
    },

    priorityBadge(p) {
        const m = { 'CRITICAL':'danger', 'HIGH':'warning', 'MEDIUM':'info', 'LOW':'muted' };
        return `<span class="badge badge-${m[p]||'muted'}">${p}</span>`;
    },

    timeFormat(iso) {
        if (!iso) return '--';
        return new Date(iso).toLocaleString();
    },

    toast(msg, type='success') {
        const t = document.getElementById('toast');
        t.textContent = msg; t.className = `toast ${type} show`;
        setTimeout(() => t.classList.remove('show'), 3000);
    },

    async loadJobs() {
        const jobs = await (await fetch(this.API)).json();
        const tbody = document.getElementById('jobs-table');
        if (jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No jobs yet. Create one!</td></tr>';
            return;
        }
        tbody.innerHTML = jobs.map(j => `
            <tr>
                <td><strong>${j.name}</strong><br><small style="color:#8b949e">${j.description||''}</small></td>
                <td>${j.schedule_type === 'CRON' ? j.cron_expression : 'Once'}</td>
                <td><code>${j.handler}</code></td>
                <td>${this.priorityBadge(j.priority)}</td>
                <td>${this.statusBadge(j.status)}</td>
                <td>${this.timeFormat(j.next_run_at)}</td>
                <td>
                    <button class="btn btn-sm" onclick="Jobs.triggerJob('${j.id}')">Run</button>
                    <button class="btn btn-sm" onclick="Jobs.editJob('${j.id}')">Edit</button>
                    <button class="btn btn-sm btn-danger" onclick="Jobs.deleteJob('${j.id}')">Delete</button>
                </td>
            </tr>
        `).join('');
    },

    toggleScheduleFields() {
        const t = document.getElementById('job-schedule').value;
        document.getElementById('cron-group').style.display = t === 'CRON' ? '' : 'none';
        document.getElementById('runday-group').style.display = t === 'ONE_TIME' ? '' : 'none';
    },

    openCreateModal() {
        document.getElementById('modal-title').textContent = 'Create Job';
        document.getElementById('edit-job-id').value = '';
        document.getElementById('job-name').value = '';
        document.getElementById('job-desc').value = '';
        document.getElementById('job-cron').value = '';
        document.getElementById('job-schedule').value = 'CRON';
        document.getElementById('job-handler').value = 'billing_reconciliation';
        document.getElementById('job-priority').value = 'MEDIUM';
        document.getElementById('job-timeout').value = '300';
        document.getElementById('job-retries').value = '1';
        this.toggleScheduleFields();
        document.getElementById('modal').classList.add('active');
    },

    closeModal() {
        document.getElementById('modal').classList.remove('active');
    },

    async saveJob() {
        const id = document.getElementById('edit-job-id').value;
        const data = {
            name: document.getElementById('job-name').value,
            description: document.getElementById('job-desc').value,
            schedule_type: document.getElementById('job-schedule').value,
            handler: document.getElementById('job-handler').value,
            priority: document.getElementById('job-priority').value,
            timeout_seconds: parseInt(document.getElementById('job-timeout').value),
            retry_limit: parseInt(document.getElementById('job-retries').value),
        };
        if (data.schedule_type === 'CRON') {
            data.cron_expression = document.getElementById('job-cron').value;
            data.timezone = 'UTC';
        } else {
            data.run_at = document.getElementById('job-runat').value || new Date().toISOString();
        }

        const url = id ? `${this.API}/${id}/` : this.API;
        const method = id ? 'PATCH' : 'POST';
        const res = await fetch(url, {
            method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)
        });
        if (res.ok) {
            this.toast(id ? 'Job updated' : 'Job created');
            this.closeModal(); this.loadJobs();
        } else {
            const err = await res.json();
            this.toast('Error: ' + JSON.stringify(err), 'error');
        }
    },

    async editJob(id) {
        const job = await (await fetch(`${this.API}/${id}/`)).json();
        document.getElementById('modal-title').textContent = 'Edit Job';
        document.getElementById('edit-job-id').value = id;
        document.getElementById('job-name').value = job.name;
        document.getElementById('job-desc').value = job.description;
        document.getElementById('job-schedule').value = job.schedule_type;
        document.getElementById('job-cron').value = job.cron_expression || '';
        document.getElementById('job-handler').value = job.handler;
        document.getElementById('job-priority').value = job.priority;
        document.getElementById('job-timeout').value = job.timeout_seconds;
        document.getElementById('job-retries').value = job.retry_limit;
        this.toggleScheduleFields();
        document.getElementById('modal').classList.add('active');
    },

    async deleteJob(id) {
        if (!confirm('Soft-delete this job?')) return;
        await fetch(`${this.API}/${id}/`, { method: 'DELETE' });
        this.toast('Job deleted'); this.loadJobs();
    },

    async triggerJob(id) {
        const res = await fetch(`${this.API}/${id}/trigger/`, { method: 'POST' });
        if (res.ok) { this.toast('Job triggered'); }
        else { this.toast('Failed to trigger', 'error'); }
    },

    init() {
        this.loadJobs();
    }
};
