<template>
  <div id="app">
    <el-container style="height: 100vh;">
      <!-- 左侧导航栏 -->
      <el-aside width="220px" class="sidebar">
        <!-- 品牌区 -->
        <div class="brand">
          <div class="brand-icon">
            <img src="../public/favicon.svg" alt="">
          </div>
          <div class="brand-info">
            <span class="brand-sub">AIOps 智能告警处理系统</span>
          </div>
          <div class="brand-status">
            <span class="status-dot" :class="connected ? 'dot-online' : 'dot-offline'"></span>
            <span class="status-text">{{ connected ? '已连接' : '未连接' }}</span>
            <span class="brand-model">DeepSeek V4</span>
          </div>
        </div>

        <!-- 导航菜单 -->
        <el-menu
          :default-active="currentView"
          @select="handleMenuSelect"
          class="nav-menu"
        >
          <el-menu-item index="dashboard">
            <i class="el-icon-s-data"></i>
            <span>数据中心</span>
          </el-menu-item>
          <el-menu-item index="alerts">
            <i class="el-icon-warning"></i>
            <span>告警中心</span>
          </el-menu-item>
          <el-menu-item index="knowledge">
            <i class="el-icon-document"></i>
            <span>知识库</span>
          </el-menu-item>
          <el-menu-item index="chat">
            <i class="el-icon-chat-dot-round"></i>
            <span>智能对话</span>
          </el-menu-item>
        </el-menu>

        <!-- 管理员区（底部） -->
        <div class="admin-area">
          <el-dropdown @command="handleAdminCommand" trigger="click">
            <div class="admin-info">
              <i class="el-icon-user-solid"></i>
              <span>admin</span>
              <i class="el-icon-arrow-down" style="margin-left: 4px;"></i>
            </div>
            <el-dropdown-menu slot="dropdown">
              <el-dropdown-item command="profile">个人设置</el-dropdown-item>
              <el-dropdown-item command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </el-dropdown>
        </div>
      </el-aside>

      <!-- 右侧主内容 -->
      <el-container>
        <el-main class="main-content">
          <!-- 仪表盘 -->
          <Dashboard v-if="currentView === 'dashboard'" @view-alert="goToDetail" />

          <!-- 告警中心 -->
          <AlertCenter
            v-else-if="currentView === 'alerts'"
            @create="currentView = 'create-alert'"
            @view-detail="goToDetail"
            @view-dashboard="currentView = 'dashboard'"
          />

          <!-- 创建告警 -->
          <CreateAlert
            v-else-if="currentView === 'create-alert'"
            @created="onAlertCreated"
            @back="currentView = 'alerts'"
          />

          <!-- 告警详情 -->
          <AlertDetail
            v-else-if="currentView === 'alert-detail'"
            :alert-id="selectedAlertId"
            @back="currentView = 'alerts'"
          />

          <!-- 知识库 -->
          <KnowledgeBase v-else-if="currentView === 'knowledge'" />

          <!-- 辅助对话 -->
          <ChatPanel v-else-if="currentView === 'chat'" />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script>
import Dashboard from './views/Dashboard.vue'
import AlertCenter from './views/AlertCenter.vue'
import CreateAlert from './views/CreateAlert.vue'
import AlertDetail from './views/AlertDetail.vue'
import KnowledgeBase from './views/KnowledgeBase.vue'
import ChatPanel from './views/ChatPanel.vue'

export default {
  name: 'App',
  components: { Dashboard, AlertCenter, CreateAlert, AlertDetail, KnowledgeBase, ChatPanel },
  data() {
    return {
      currentView: 'dashboard',
      selectedAlertId: null,
      connected: false,
    }
  },
  mounted() {
    this.checkHealth()
  },
  methods: {
    async checkHealth() {
      try {
        const res = await fetch('/health')
        this.connected = res.ok
      } catch {
        this.connected = false
      }
    },
    handleMenuSelect(index) {
      this.currentView = index
    },
    goToDetail(alertId) {
      this.selectedAlertId = alertId
      this.currentView = 'alert-detail'
    },
    onAlertCreated(alertId) {
      this.selectedAlertId = alertId
      this.currentView = 'alert-detail'
    },
    handleAdminCommand(cmd) {
      if (cmd === 'logout') {
        this.$message.info('已退出登录')
      } else {
        this.$message.info('个人设置')
      }
    },
  },
}
</script>

<style>
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f5;
}
#app { height: 100vh; }

/* 侧边栏 - 深色专业风格 */
.sidebar {
  background: #0d1f3c;
  display: flex;
  flex-direction: column;
  color: #fff;
  user-select: none;
  border-right: 1px solid rgba(255,255,255,0.06);
}

/* 品牌区 */
.brand {
  text-align: center;
  padding: 22px 20px 18px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.brand-icon { font-size: 30px; display: block; margin-bottom: 10px; }
.brand-sub {
  font-size: 10px;
  opacity: 0.45;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  display: block;
  margin-top: 2px;
}
.brand-status {
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: 11px;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.dot-online { background: #52c41a; box-shadow: 0 0 4px rgba(82, 196, 26, 0.6); }
.dot-offline { background: #f56c6c; box-shadow: 0 0 4px rgba(245, 108, 108, 0.6); }
.status-text {
  color: rgba(255,255,255,0.45);
  margin-right: 2px;
}
.brand-model {
  font-size: 10px;
  background: rgba(255,255,255,0.07);
  padding: 1px 6px;
  border-radius: 3px;
  color: rgba(255,255,255,0.35);
  letter-spacing: 0.3px;
}

/* 导航 - 高对比度，清晰可读 */
.nav-menu {
  flex: 1;
  border-right: none !important;
  overflow-y: auto;
  padding: 8px 0;
  background: transparent !important;
}
.nav-menu .el-menu-item {
  color: rgba(255,255,255,0.85) !important;
  height: 44px;
  line-height: 44px;
  margin: 2px 10px;
  border-radius: 6px;
  transition: all 0.15s ease;
}
.nav-menu .el-menu-item i {
  color: rgba(255,255,255,0.5) !important;
  font-size: 16px;
  margin-right: 8px;
}
.nav-menu .el-menu-item:hover {
  background: rgba(255,255,255,0.08) !important;
  color: #fff !important;
}
.nav-menu .el-menu-item:hover i {
  color: rgba(255,255,255,0.85) !important;
}
.nav-menu .el-menu-item.is-active {
  background: linear-gradient(90deg, rgba(64,158,255,0.18) 0%, rgba(64,158,255,0.04) 100%) !important;
  color: #409eff !important;
  font-weight: 600;
  box-shadow: inset 3px 0 0 #409eff;
}
.nav-menu .el-menu-item.is-active i {
  color: #409eff !important;
}

/* 管理员区 */
.admin-area {
  padding: 14px 20px;
  border-top: 1px solid rgba(255,255,255,0.06);
  cursor: pointer;
}
.admin-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: rgba(255,255,255,0.6);
  font-size: 13px;
  transition: color 0.15s;
}
.admin-info:hover { color: rgba(255,255,255,0.9); }
.admin-info i:first-child {
  font-size: 18px;
  color: rgba(255,255,255,0.35);
}

/* 主内容 */
.main-content {
  background: #f0f2f5;
  padding: 24px;
  overflow-y: auto;
  height: 100vh;
}
</style>
