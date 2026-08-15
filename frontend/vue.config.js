const { defineConfig } = require('@vue/cli-service')
module.exports = defineConfig({
  transpileDependencies: true,
  devServer: {
    port: 8081,
    proxy: {
      '/api': {
        target: 'http://localhost:9092',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:9092',
        changeOrigin: true,
      },
    },
  },
})
