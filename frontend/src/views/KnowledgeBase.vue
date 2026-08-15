<template>
  <div class="knowledge-base">
    <div class="page-header">
      <div>
        <h2>知识库</h2>
        <p class="page-desc">故障案例文档管理（Chroma 向量检索），支持 Markdown 格式上传</p>
      </div>
      <el-tag v-if="stats.ready" type="success" effect="dark">向量库已就绪</el-tag>
      <el-tag v-else type="danger" effect="dark">向量库不可用</el-tag>
    </div>

    <!-- 上传区域 -->
    <el-card shadow="hover" class="upload-card">
      <div slot="header">
        <strong>上传故障案例</strong>
        <el-button type="text" size="small" class="template-btn" @click="downloadTemplate">
          <i class="el-icon-download"></i> 下载模板
        </el-button>
        <el-button type="text" size="small" class="template-btn" @click="viewTemplate">
          <i class="el-icon-view"></i> 查看模板
        </el-button>
      </div>
      <el-upload
        drag
        :http-request="handleUploadRequest"
        :show-file-list="false"
        accept=".md,.txt,.markdown"
      >
        <i class="el-icon-upload"></i>
        <div class="el-upload__text">将文件拖到此处，或<em>点击上传</em></div>
        <div class="el-upload__tip" slot="tip">
          支持 Markdown (.md)、文本 (.txt)，单个文件不超过 10MB。<br />
          不确定格式？点击上方 <strong>下载模板</strong> 获取标准模板，或 <strong>查看模板</strong> 预览格式。
        </div>
      </el-upload>
    </el-card>

    <!-- 统计 -->
    <div class="stat-row">
      <div class="stat-card">
        <span class="stat-label">知识文档</span>
        <span class="stat-value">{{ stats.doc_count }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">故障案例</span>
        <span class="stat-value">{{ stats.case_count }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">检索方式</span>
        <span class="stat-value stat-text">Chroma 向量检索</span>
      </div>
    </div>

    <!-- 文档列表 -->
    <el-card shadow="hover">
      <div slot="header"><strong>知识库文档 ({{ documents.length }})</strong></div>

      <el-table :data="documents" stripe style="width: 100%" v-if="documents.length > 0">
        <el-table-column label="文件名" min-width="260">
          <template slot-scope="{ row }">
            <i class="el-icon-document" style="margin-right: 8px; color: #409eff;"></i>
            {{ row.doc_name }}
          </template>
        </el-table-column>
        <el-table-column prop="case_count" label="案例数" width="90">
          <template slot-scope="{ row }">
            <el-tag size="mini" type="info">{{ row.case_count }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="上传时间" width="200" />
        <el-table-column label="操作" width="190">
          <template slot-scope="{ row }">
            <el-button type="text" size="small" @click="viewDoc(row)">查看</el-button>
            <el-button type="text" size="small" @click="downloadDoc(row)">
              <i class="el-icon-download"></i> 下载
            </el-button>
            <el-button type="text" size="small" style="color: #f56c6c;" @click="deleteDoc(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="知识库暂无文档，请上传故障案例" />
    </el-card>

    <!-- 查看模板弹窗 -->
    <el-dialog title="知识文件模板" :visible.sync="templateDialogVisible" width="60%" top="6vh">
      <div class="template-body">{{ templateContent }}</div>
      <div slot="footer">
        <el-button @click="templateDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="downloadTemplate">
          <i class="el-icon-download"></i> 下载模板文件
        </el-button>
      </div>
    </el-dialog>

    <!-- 查看文档弹窗 -->
    <el-dialog
      :title="viewingDoc ? viewingDoc.doc_name : '文档预览'"
      :visible.sync="docDialogVisible"
      width="60%"
      top="6vh"
    >
      <div v-loading="docLoading" class="doc-preview">{{ docContent || '加载中...' }}</div>
      <div slot="footer">
        <el-button @click="docDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="downloadDoc(viewingDoc)" v-if="viewingDoc">
          <i class="el-icon-download"></i> 下载文档
        </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script>
export default {
  data() {
    return {
      uploading: false,
      documents: [],
      stats: { ready: false, doc_count: 0, case_count: 0 },
      templateDialogVisible: false,
      templateContent: '',
      docDialogVisible: false,
      docLoading: false,
      docContent: '',
      viewingDoc: null,
    }
  },
  mounted() {
    this.loadDocuments()
    this.loadStats()
  },
  methods: {
    // ── 上传 ──
    async handleUploadRequest(options) {
      const fd = new FormData()
      fd.append('file', options.file)
      this.uploading = true
      try {
        const res = await fetch('/api/v1/knowledge/upload', { method: 'POST', body: fd })
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          throw new Error(err.detail || '上传失败')
        }
        const data = await res.json()
        this.$message.success(`「${data.doc_name}」上传成功，导入 ${data.case_count} 个案例`)
        options.onSuccess(data)
        await this.loadDocuments()
        await this.loadStats()
      } catch (e) {
        this.$message.error('上传失败: ' + e.message)
        options.onError(e)
      } finally {
        this.uploading = false
      }
    },

    // ── 模板 ──
    async getTemplate() {
      const res = await fetch('/api/v1/knowledge/template')
      if (!res.ok) throw new Error('获取模板失败')
      return res.json()
    },
    async downloadTemplate() {
      try {
        const data = await this.getTemplate()
        const blob = new Blob([data.content], { type: 'text/markdown;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = data.name
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      } catch (e) {
        this.$message.error('下载模板失败: ' + e.message)
      }
    },
    async viewTemplate() {
      try {
        const data = await this.getTemplate()
        this.templateContent = data.content
        this.templateDialogVisible = true
      } catch (e) {
        this.$message.error('获取模板失败: ' + e.message)
      }
    },

    // ── 文档查看 / 下载 / 删除 ──
    async getDocContent(row) {
      const res = await fetch(`/api/v1/knowledge/documents/${row.doc_id}/content`)
      if (!res.ok) throw new Error('获取文档内容失败')
      return res.json()
    },
    async viewDoc(row) {
      this.viewingDoc = row
      this.docDialogVisible = true
      this.docContent = ''
      this.docLoading = true
      try {
        const data = await this.getDocContent(row)
        this.docContent = data.content
      } catch (e) {
        this.docContent = ''
        this.$message.error(e.message)
      }
      this.docLoading = false
    },
    async downloadDoc(row) {
      try {
        const data = await this.getDocContent(row)
        const blob = new Blob([data.content], { type: 'text/markdown;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = data.doc_name
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      } catch (e) {
        this.$message.error('下载失败: ' + e.message)
      }
    },

    // ── 文档列表 / 删除 ──
    async loadDocuments() {
      try {
        const res = await fetch('/api/v1/knowledge/documents')
        if (res.ok) this.documents = await res.json()
      } catch (e) {
        this.$message.error('加载文档失败: ' + e.message)
      }
    },
    async loadStats() {
      try {
        const res = await fetch('/api/v1/knowledge/stats')
        if (res.ok) this.stats = await res.json()
      } catch (e) {
        this.$message.error('加载统计失败: ' + e.message)
      }
    },
    deleteDoc(row) {
      this.$confirm(`确定删除「${row.doc_name}」？`, '提示', { type: 'warning' }).then(async () => {
        const res = await fetch(`/api/v1/knowledge/documents/${row.doc_id}`, { method: 'DELETE' })
        if (res.ok) {
          this.$message.success('已删除')
          await this.loadDocuments()
          await this.loadStats()
        } else {
          this.$message.error('删除失败')
        }
      }).catch(() => {})
    },
  },
}
</script>

<style scoped>
.knowledge-base { max-width: 1200px; margin: 0 auto; }
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.page-header h2 { margin: 0; font-size: 22px; }
.page-desc { margin: 4px 0 0; color: #999; font-size: 13px; }
.upload-card { margin-bottom: 20px; }
.template-btn { margin-left: 12px; color: #409eff; }

/* 统计 */
.stat-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 20px;
}
.stat-card {
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stat-label { font-size: 12px; color: #909399; }
.stat-value { font-size: 24px; font-weight: 600; color: #1d2129; }
.stat-text { font-size: 15px; font-weight: 500; line-height: 32px; }

/* 模板 / 文档预览弹窗 */
.template-body,
.doc-preview {
  white-space: pre-wrap;
  line-height: 1.8;
  font-size: 13px;
  padding: 14px;
  background: #fafafa;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  max-height: 60vh;
  overflow-y: auto;
}
</style>
