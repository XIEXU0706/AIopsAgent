<template>
  <div class="chat-panel">
    <div class="page-header">
      <div>
        <h2>智能对话</h2>
        <p class="page-desc">基于分层记忆的智能对话，支持历史上下文自动压缩</p>
      </div>
      <div>
        <el-button size="small" @click="newSession" :disabled="creating">新建会话</el-button>
        <el-button size="small" icon="el-icon-refresh" @click="loadSessions">刷新会话</el-button>
      </div>
    </div>

    <el-row :gutter="20" style="height: calc(100vh - 160px);">
      <!-- 会话列表 -->
      <el-col :span="6">
        <el-card shadow="hover" style="height: 100%; overflow-y: auto;">
          <div slot="header"><strong>会话列表</strong></div>
          <div
            v-for="s in sessions"
            :key="s.id"
            :class="['session-item', { active: s.id === currentSessionId }]"
            @click="switchSession(s.id)"
          >
            <div class="session-title">{{ s.title }}</div>
            <div class="session-time">{{ s.create_time ? formatTime(s.create_time) : '' }}</div>
            <el-button
              class="session-del"
              size="mini"
              type="text"
              icon="el-icon-delete"
              :disabled="deleting"
              @click.stop="deleteSession(s.id)"
            ></el-button>
          </div>
          <el-empty v-if="sessions.length === 0" description="暂无会话" :image-size="60" />
        </el-card>
      </el-col>

      <!-- 聊天区域 -->
      <el-col :span="18">
        <el-card shadow="hover" style="height: 100%; display: flex; flex-direction: column;">
          <!-- 消息列表 -->
          <div class="message-list" ref="messageList">
            <div v-if="messages.length === 0" class="empty-chat">
              <el-empty description="发送消息开始对话" :image-size="80" />
            </div>
            <div
              v-for="(msg, i) in messages"
              :key="i"
              :class="['message', msg.role === 'user' ? 'message-user' : 'message-ai']"
            >
              <div class="message-avatar">
                <el-avatar :size="36" :icon="msg.role === 'user' ? 'el-icon-user-solid' : 'el-icon-monitor'" />
              </div>
              <div class="message-bubble">
                <div class="message-content markdown-body" v-html="renderMarkdown(msg.content)"></div>
                <div class="message-time" v-if="msg.timestamp">{{ formatTime(msg.timestamp) }}</div>
              </div>
            </div>
            <div v-if="streaming" class="message message-ai">
              <div class="message-avatar">
                <el-avatar :size="36" icon="el-icon-monitor" />
              </div>
              <div class="message-bubble">
                <div class="message-content markdown-body">
                  <template v-if="streamingText"><span v-html="renderMarkdown(streamingText)"></span><span class="cursor-blink">|</span></template>
                  <template v-else>
                    <span class="thinking-text">正在思考中</span>
                    <span class="thinking-dots"><i>.</i><i>.</i><i>.</i></span>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <!-- 输入区 -->
          <div class="input-area">
            <el-input
              v-model="inputText"
              type="textarea"
              :rows="3"
              placeholder="输入您的问题，例如：MySQL 连接数过高怎么办？"
              @keydown.enter.native.prevent="sendMessage"
              :disabled="!currentSessionId || streaming"
            />
            <div class="input-actions">
              <span class="context-hint" v-if="messageCount > 0">
                当前会话共 {{ messageCount }} 条消息
                <el-tag size="mini" type="info" v-if="messageCount > 10" style="margin-left: 4px;">
                  已压缩
                </el-tag>
              </span>
              <el-button
                type="primary"
                @click="sendMessage"
                :loading="streaming"
                :disabled="!inputText.trim() || !currentSessionId"
              >
                {{ streaming ? '生成中...' : '发送' }}
              </el-button>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script>
export default {
  data() {
    return {
      sessions: [],
      currentSessionId: null,
      messages: [],
      inputText: '',
      streaming: false,
      streamingText: '',
      creating: false,
      deleting: false,
      messageCount: 0,
    }
  },
  mounted() {
    this.loadSessions()
  },
  methods: {
    formatTime(ts) {
      try {
        return new Date(ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      } catch {
        return ts
      }
    },
    // 零依赖 Markdown 渲染（不引入 marked/dompurify，避免安装权限问题）
    renderMarkdown(text) {
      if (!text) return ''
      let html = this._escapeHtml(text)
      // 代码块 ```lang ... ```
      html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) => {
        return '<pre class="md-pre"><code>' + code.replace(/\n$/, '') + '</code></pre>'
      })
      // 行内代码 `code`
      html = html.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
      // 标题 # ## ###
      html = html.replace(/^###\s+(.*)$/gm, '<h3>$1</h3>')
      html = html.replace(/^##\s+(.*)$/gm, '<h2>$1</h2>')
      html = html.replace(/^#\s+(.*)$/gm, '<h1>$1</h1>')
      // 粗体 **text**
      html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      // 无序列表 - / *
      html = html.replace(/^(?:[-*])\s+(.*)$/gm, '<li>$1</li>')
      html = html.replace(/(<li>[\s\S]*?<\/li>)(?:\n|<li>)/g, '<ul>$1</ul>')
      // 有序列表 1.
      html = html.replace(/^\d+\.\s+(.*)$/gm, '<li>$1</li>')
      // 引用 >
      html = html.replace(/^>\s+(.*)$/gm, '<blockquote>$1</blockquote>')
      // 链接 [text](url)
      html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      // 换行
      html = html.replace(/\n{2,}/g, '<br><br>').replace(/\n/g, '<br>')
      return html
    },
    _escapeHtml(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
    },
    async loadSessions() {
      try {
        const res = await fetch('/api/v1/sessions')
        if (res.ok) {
          this.sessions = await res.json()
          // 自动选中第一个
          if (this.sessions.length > 0 && !this.currentSessionId) {
            this.switchSession(this.sessions[0].id)
          }
        }
      } catch { /* ignore */ }
    },
    async newSession() {
      this.creating = true
      try {
        const res = await fetch('/api/v1/sessions', { method: 'POST' })
        if (res.ok) {
          const data = await res.json()
          this.sessions.unshift({ id: data.id, title: data.title, create_time: new Date().toISOString() })
          this.switchSession(data.id)
        }
      } catch { /* ignore */ }
      this.creating = false
    },
    async deleteSession(sessionId) {
      if (!confirm('确定删除该会话及其所有消息吗？')) return
      this.deleting = true
      try {
        const res = await fetch(`/api/v1/sessions/${sessionId}`, { method: 'DELETE' })
        if (res.ok) {
          this.sessions = this.sessions.filter(s => s.id !== sessionId)
          if (this.currentSessionId === sessionId) {
            this.currentSessionId = null
            this.messages = []
            this.messageCount = 0
            if (this.sessions.length > 0) this.switchSession(this.sessions[0].id)
          }
        }
      } catch { /* ignore */ }
      this.deleting = false
    },
    async switchSession(sessionId) {
      this.currentSessionId = sessionId
      this.messages = []
      this.messageCount = 0
      try {
        const res = await fetch(`/api/v1/sessions/${sessionId}/messages`)
        if (res.ok) {
          const msgs = await res.json()
          // 过滤掉 system 类型的摘要消息
          this.messages = msgs.filter(m => m.role !== 'system')
          this.messageCount = msgs.length
        }
      } catch { /* ignore */ }
      this.$nextTick(() => this.scrollToBottom())
    },
    async sendMessage() {
      const text = this.inputText.trim()
      if (!text || !this.currentSessionId) return

      // 用户消息立即显示
      this.messages.push({ role: 'user', content: text, timestamp: new Date().toISOString() })
      this.inputText = ''
      this.streaming = true
      this.streamingText = ''
      this.$nextTick(() => this.scrollToBottom())

      try {
        const res = await fetch('/api/v1/chat/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: this.currentSessionId, query: text }),
        })

        if (res.ok) {
          const reader = res.body.getReader()
          const decoder = new TextDecoder()

          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            const chunk = decoder.decode(value)
            // 解析 SSE data: 行
            for (const line of chunk.split('\n')) {
              if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6))
                  if (data.content) {
                    this.streamingText += data.content
                    this.$nextTick(() => this.scrollToBottom())
                  }
                } catch { /* ignore parse errors for partial lines */ }
              }
            }
          }

          // 流完成，加入消息列表
          if (this.streamingText) {
            this.messages.push({
              role: 'ai',
              content: this.streamingText,
              timestamp: new Date().toISOString(),
            })
            this.messageCount++
          }
        }
      } catch { /* ignore */ }

      this.streaming = false
      this.streamingText = ''
      this.messageCount++
      this.$nextTick(() => this.scrollToBottom())

      // 刷新会话标题
      this.loadSessions()
    },
    scrollToBottom() {
      const el = this.$refs.messageList
      if (el) el.scrollTop = el.scrollHeight
    },
  },
}
</script>

<style scoped>
.chat-panel { height: 100%; }

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-header h2 { margin: 0; font-size: 22px; }
.page-desc { margin: 4px 0 0; color: #999; font-size: 13px; }

/* 会话列表 */
.session-item {
  position: relative;
  padding: 12px 36px 12px 12px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  border-radius: 6px;
  margin-bottom: 4px;
}
.session-item:hover { background: #f5f7fa; }
.session-item.active { background: #e6f7ff; }
.session-title { font-weight: 500; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-time { font-size: 11px; color: #bbb; margin-top: 2px; }
.session-del {
  position: absolute;
  top: 50%;
  right: 6px;
  transform: translateY(-50%);
  color: #c0c4cc;
}
.session-del:hover { color: #f56c6c; }

/* 消息区域 */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #fafafa;
  border-radius: 4px;
  margin-bottom: 12px;
}
.empty-chat { display: flex; align-items: center; justify-content: center; height: 100%; }

.message {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}
.message-user { flex-direction: row-reverse; }
.message-avatar { flex-shrink: 0; }
.message-bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
}
.message-user .message-bubble {
  background: #1890ff;
  color: #fff;
  border-bottom-right-radius: 4px;
}
.message-ai .message-bubble {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-bottom-left-radius: 4px;
}
.message-content { white-space: pre-wrap; word-break: break-word; }
/* Markdown 渲染样式 */
.markdown-body { white-space: normal; line-height: 1.6; }
.markdown-body p { margin: 0 0 8px; }
.markdown-body p:last-child { margin-bottom: 0; }
.markdown-body pre {
  background: #f6f8fa; border-radius: 6px; padding: 12px; overflow-x: auto;
  font-size: 13px; margin: 8px 0;
}
.markdown-body code {
  background: #f0f2f5; border-radius: 4px; padding: 2px 4px; font-size: 13px;
}
.markdown-body pre code { background: transparent; padding: 0; }
.markdown-body ul, .markdown-body ol { padding-left: 20px; margin: 8px 0; }
.markdown-body table { border-collapse: collapse; margin: 8px 0; }
.markdown-body th, .markdown-body td { border: 1px solid #ebeef5; padding: 6px 10px; }
.markdown-body blockquote {
  border-left: 4px solid #dcdfe6; padding-left: 12px; color: #909399; margin: 8px 0;
}
.markdown-body h1, .markdown-body h2, .markdown-body h3 { margin: 10px 0 6px; }
.message-time { font-size: 11px; color: rgba(0,0,0,0.35); margin-top: 4px; text-align: right; }
.message-user .message-time { color: rgba(255,255,255,0.6); }

.cursor-blink { animation: blink 1s step-end infinite; }
@keyframes blink { 50% { opacity: 0; } }

.thinking-text { color: #909399; font-size: 13px; }
.thinking-dots { margin-left: 2px; }
.thinking-dots i { font-style: normal; color: #909399; animation: think 1.2s infinite; }
.thinking-dots i:nth-child(2) { animation-delay: 0.2s; }
.thinking-dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes think { 0%, 80%, 100% { opacity: 0; } 40% { opacity: 1; } }

/* 输入区 */
.input-area {
  flex-shrink: 0;
}
.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.context-hint { font-size: 12px; color: #999; }
</style>
