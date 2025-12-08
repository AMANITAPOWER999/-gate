class TradingDashboard {
    constructor() {
        this.lastUpdateTime = null;
        this.isUpdating = false;
        
        this.bindEvents();
        this.startDataUpdates();
        
        // Initial load
        this.updateDashboard();
        this.loadTelegramInfo();
    }


    bindEvents() {
        // Bot control buttons
        document.getElementById('start-bot').addEventListener('click', () => {
            this.startBot();
        });

        document.getElementById('stop-bot').addEventListener('click', () => {
            this.stopBot();
        });

        document.getElementById('close-position').addEventListener('click', () => {
            this.closePosition();
        });


        document.getElementById('debug-sar').addEventListener('click', () => {
            this.toggleDebugInfo();
        });
        
        // Telegram test button
        document.getElementById('test-telegram').addEventListener('click', () => {
            this.sendTestTelegramMessage();
        });
    }

    async startBot() {
        try {
            const response = await fetch('/api/start_bot', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message);
            } else {
                this.showNotification('error', data.error);
            }
        } catch (error) {
            this.showNotification('error', 'Ошибка связи с сервером');
            console.error('Start bot error:', error);
        }
    }

    async stopBot() {
        try {
            const response = await fetch('/api/stop_bot', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message);
            } else {
                this.showNotification('error', data.error);
            }
        } catch (error) {
            this.showNotification('error', 'Ошибка связи с сервером');
            console.error('Stop bot error:', error);
        }
    }

    async closePosition() {
        if (!confirm('Вы уверены, что хотите закрыть текущую позицию?')) {
            return;
        }

        try {
            const response = await fetch('/api/close_position', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message);
            } else {
                this.showNotification('error', data.error);
            }
        } catch (error) {
            this.showNotification('error', 'Ошибка связи с сервером');
            console.error('Close position error:', error);
        }
    }

    async sendTestTelegramMessage() {
        try {
            const response = await fetch('/api/send_test_message', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message);
            } else {
                this.showNotification('error', data.error);
            }
        } catch (error) {
            this.showNotification('error', 'Ошибка отправки тестового сообщения');
            console.error('Test telegram message error:', error);
        }
    }

    async loadTelegramInfo() {
        try {
            const response = await fetch('/api/telegram_info');
            const data = await response.json();
            
            if (response.ok) {
                this.updateTelegramInfo(data);
            } else {
                console.error('Failed to load telegram info:', data.error);
            }
        } catch (error) {
            console.error('Load telegram info error:', error);
        }
    }

    updateTelegramInfo(data) {
        // Update owner ID
        const ownerIdElement = document.getElementById('owner-id');
        if (ownerIdElement) {
            ownerIdElement.textContent = data.owner_id !== 'NOT_SET' ? data.owner_id : 'НЕ УСТАНОВЛЕН';
        }

        // Update webhook status
        const webhookStatusElement = document.getElementById('webhook-status');
        if (webhookStatusElement) {
            let statusText = 'Не настроен';
            let statusClass = 'bg-danger';
            
            switch(data.webhook_status) {
                case 'configured':
                    statusText = 'Настроен';
                    statusClass = 'bg-success';
                    break;
                case 'not_set':
                    statusText = 'Не установлен';
                    statusClass = 'bg-warning';
                    break;
                case 'error':
                    statusText = 'Ошибка';
                    statusClass = 'bg-danger';
                    break;
                default:
                    statusText = 'Не настроен';
                    statusClass = 'bg-secondary';
            }
            
            webhookStatusElement.textContent = statusText;
            webhookStatusElement.className = `badge ${statusClass}`;
        }
    }

    async updateDashboard() {
        if (this.isUpdating) return;
        
        this.isUpdating = true;
        
        try {
            const response = await fetch('/api/status');
            
            if (!response.ok) {
                throw new Error('Failed to fetch status');
            }
            
            const data = await response.json();
            
            // Update basic info
            this.updateBasicInfo(data);
            
            // Update SAR directions
            this.updateSARDirections(data.directions);
            
            // Update position info
            this.updatePositionInfo(data);
            
            // Update trade history
            this.updateTradeHistory(data.trades);
            
            
            this.lastUpdateTime = new Date();
            
        } catch (error) {
            console.error('Update dashboard error:', error);
            this.showNotification('error', 'Ошибка обновления данных');
        } finally {
            this.isUpdating = false;
        }
    }

    updateBasicInfo(data) {
        // Bot status
        const botStatus = document.getElementById('bot-status');
        const paperMode = document.getElementById('paper-mode');
        
        if (data.bot_running) {
            botStatus.textContent = 'РАБОТАЕТ';
            botStatus.className = 'badge bg-success pulse';
        } else {
            botStatus.textContent = 'ОСТАНОВЛЕН';
            botStatus.className = 'badge bg-danger';
        }
        
        paperMode.style.display = data.paper_mode ? 'inline' : 'none';
        
        // Balance and price
        document.getElementById('balance').textContent = `$${data.balance.toFixed(2)}`;
        document.getElementById('available').textContent = `$${data.available.toFixed(2)}`;
        document.getElementById('current-price').textContent = `$${data.current_price.toFixed(2)}`;
        
        // Position status
        const positionStatus = document.getElementById('position-status');
        if (data.in_position) {
            positionStatus.textContent = `${data.position.side.toUpperCase()}`;
            positionStatus.className = data.position.side === 'long' ? 'text-profit' : 'text-loss';
        } else {
            positionStatus.textContent = 'Нет позиции';
            positionStatus.className = 'text-muted';
        }
    }

    updateSARDirections(directions) {
        console.log('updateSARDirections called with:', directions);
        const timeframes = ['1m', '5m', '15m'];
        let allSame = true;
        let commonDirection = null;
        
        timeframes.forEach(tf => {
            const element = document.getElementById(`sar-${tf}`);
            const container = document.getElementById(`sar-${tf}-container`);
            const direction = directions[tf];
            
            console.log(`Processing ${tf}: element=${element}, container=${container}, direction=${direction}`);
            
            if (direction) {
                // Update badge text and styling
                if (element) {
                    element.textContent = direction === 'long' ? '🟢 ЛОНГ' : '🔴 ШОРТ';
                    element.className = direction === 'long' ? 'badge sar-badge bg-success' : 'badge sar-badge bg-danger';
                }
                
                // Update container styling
                if (container) {
                    container.className = direction === 'long' ? 'sar-indicator mb-3 signal-long' : 'sar-indicator mb-3 signal-short';
                }
                
                if (commonDirection === null) {
                    commonDirection = direction;
                } else if (commonDirection !== direction) {
                    allSame = false;
                }
            } else {
                if (element) {
                    element.textContent = '⚪ N/A';
                    element.className = 'badge sar-badge bg-secondary';
                }
                
                // Reset container styling
                if (container) {
                    container.className = 'sar-indicator mb-3';
                }
                allSame = false;
            }
        });
        
        // Update signal status
        const signalStatus = document.getElementById('signal-status');
        if (allSame && commonDirection) {
            const signalText = commonDirection === 'long' ? '🚀 СИГНАЛ: ЛОНГ' : '📉 СИГНАЛ: ШОРТ';
            signalStatus.textContent = signalText;
            signalStatus.className = commonDirection === 'long' ? 
                'badge signal-badge bg-success glow-success' : 
                'badge signal-badge bg-danger glow-danger';
        } else {
            signalStatus.textContent = '⏸️ НЕТ СИГНАЛА';
            signalStatus.className = 'badge signal-badge bg-secondary';
        }
    }

    updatePositionInfo(data) {
        const noPosition = document.getElementById('no-position');
        const currentPosition = document.getElementById('current-position');
        
        if (data.in_position && data.position) {
            noPosition.classList.add('d-none');
            currentPosition.classList.remove('d-none');
            
            const position = data.position;
            
            // Update position details
            const positionSide = document.getElementById('pos-side');
            positionSide.textContent = position.side.toUpperCase();
            positionSide.className = position.side === 'long' ? 'badge bg-success' : 'badge bg-danger';
            
            document.getElementById('pos-entry').textContent = `$${position.entry_price.toFixed(2)}`;
            document.getElementById('pos-size').textContent = `${position.size_base.toFixed(6)} ETH`;
            document.getElementById('pos-notional').textContent = `$${position.notional.toFixed(2)}`;
            
            const entryTime = new Date(position.entry_time);
            document.getElementById('pos-time').textContent = entryTime.toLocaleTimeString();
            
            // Calculate unrealized P&L
            const currentPrice = data.current_price;
            let unrealizedPnL = 0;
            
            if (position.side === 'long') {
                unrealizedPnL = (currentPrice - position.entry_price) * position.size_base;
            } else {
                unrealizedPnL = (position.entry_price - currentPrice) * position.size_base;
            }
            
            const pnlElement = document.getElementById('pos-pnl');
            pnlElement.textContent = `${unrealizedPnL >= 0 ? '+' : ''}${unrealizedPnL.toFixed(2)} USDT`;
            pnlElement.className = unrealizedPnL >= 0 ? 'text-profit' : 'text-loss';
            
        } else {
            noPosition.classList.remove('d-none');
            currentPosition.classList.add('d-none');
        }
    }

    updateTradeHistory(trades) {
        const container = document.getElementById('trades-container');
        
        if (!trades || trades.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-clock fa-2x mb-3"></i>
                    <p>Нет завершенных сделок</p>
                </div>
            `;
            return;
        }
        
        const tradesHtml = trades.map(trade => {
            const isProfit = trade.pnl >= 0;
            const tradeTime = new Date(trade.time);
            const duration = trade.duration || 'N/A';
            
            return `
                <div class="trade-item">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <span class="badge ${trade.side === 'long' ? 'bg-success' : 'bg-danger'}">${trade.side.toUpperCase()}</span>
                            <small class="text-muted ms-2">${tradeTime.toLocaleTimeString()}</small>
                        </div>
                        <div class="text-end">
                            <div class="${isProfit ? 'trade-profit' : 'trade-loss'}">
                                ${isProfit ? '+' : ''}${trade.pnl.toFixed(2)} USDT
                            </div>
                            <small class="text-muted">${duration}</small>
                        </div>
                    </div>
                    <div class="mt-2">
                        <small class="text-muted">
                            Вход: $${trade.entry_price.toFixed(2)} → Выход: $${trade.exit_price.toFixed(2)}
                        </small>
                    </div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = tradesHtml;
    }


    showNotification(type, message) {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `alert alert-${type === 'error' ? 'danger' : 'success'} alert-dismissible fade show position-fixed`;
        notification.style.top = '20px';
        notification.style.right = '20px';
        notification.style.zIndex = '9999';
        notification.style.minWidth = '300px';
        
        notification.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        document.body.appendChild(notification);
        
        // Auto remove after 5 seconds
        setTimeout(() => {
            notification.remove();
        }, 5000);
    }

    async toggleDebugInfo() {
        const debugDiv = document.getElementById('debug-info');
        const debugContent = document.getElementById('debug-content');
        
        if (debugDiv.classList.contains('d-none')) {
            // Показать debug info
            debugDiv.classList.remove('d-none');
            await this.loadDebugInfo();
        } else {
            // Скрыть debug info
            debugDiv.classList.add('d-none');
        }
    }

    async loadDebugInfo() {
        const debugContent = document.getElementById('debug-content');
        
        try {
            debugContent.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading debug data...';
            
            const response = await fetch('/api/debug_sar');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            let html = `<div class="text-warning mb-2">Время: ${new Date(data.timestamp).toLocaleTimeString()}</div>`;
            html += `<div class="text-info mb-2">Текущая цена: $${data.current_price}</div><hr>`;
            
            ['15m', '5m', '1m'].forEach(tf => {
                const sarData = data.sar_data[tf];
                if (sarData && !sarData.error) {
                    const directionColor = sarData.direction === 'long' ? 'text-success' : 'text-danger';
                    const directionText = sarData.direction === 'long' ? '🟢 LONG' : '🔴 SHORT';
                    
                    html += `<div class="mb-3">`;
                    html += `<div class="fw-bold text-primary">${tf.toUpperCase()} SAR:</div>`;
                    html += `<div class="${directionColor}">${directionText}</div>`;
                    html += `<div>Close: $${sarData.last_close}</div>`;
                    html += `<div>PSAR: $${sarData.last_psar}</div>`;
                    html += `<div>Diff: ${sarData.close_vs_psar > 0 ? '+' : ''}${sarData.close_vs_psar}</div>`;
                    
                    if (sarData.last_candles && sarData.last_candles.length > 0) {
                        const lastCandle = sarData.last_candles[sarData.last_candles.length - 1];
                        html += `<div class="text-muted small">Последняя свеча: ${lastCandle.time} OHLC(${lastCandle.open}/${lastCandle.high}/${lastCandle.low}/${lastCandle.close})</div>`;
                    }
                    html += `</div>`;
                } else {
                    html += `<div class="mb-3"><div class="fw-bold text-danger">${tf.toUpperCase()}: ${sarData?.error || 'No data'}</div></div>`;
                }
            });
            
            debugContent.innerHTML = html;
        } catch (error) {
            debugContent.innerHTML = `<div class="text-danger">Ошибка загрузки debug данных: ${error.message}</div>`;
            console.error('Debug info error:', error);
        }
    }

    startDataUpdates() {
        // Update dashboard every 3 seconds
        setInterval(() => {
            this.updateDashboard();
        }, 3000);
        
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new TradingDashboard();
});
