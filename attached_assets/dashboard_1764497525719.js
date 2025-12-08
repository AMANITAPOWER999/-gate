class TradingDashboard {
    constructor() {
        this.lastUpdateTime = null;
        this.isUpdating = false;
        this.currentTimeframe = '5m';
        this.chart = null;
        this.candlestickSeries = null;
        this.chartManuallyAdjusted = false;
        this.savedTimeRange = null;
        this.currentSymbol = 'TOP1/USDT';
        this.topPairPrice = 0;  // Store top 1 gainer price
        this.positionSymbolLocked = false;  // Track if symbol is locked by position
        this.lockedSymbol = null;  // Symbol locked during position
        
        this.initChart();
        this.bindEvents();
        this.startDataUpdates();
        
        this.updateDashboard();
        this.updateChart();
    }

    initChart() {
        const container = document.getElementById('chart-container');
        if (!container) return;
        
        this.chart = LightweightCharts.createChart(container, {
            layout: {
                textColor: '#d1d5db',
                background: { color: '#000000' }
            },
            timeScale: {
                timeVisible: true,
                secondsVisible: false,
            },
            grid: {
                vertLines: { color: 'rgba(59, 130, 246, 0.1)' },
                horzLines: { color: 'rgba(59, 130, 246, 0.1)' }
            }
        });
        
        this.candlestickSeries = this.chart.addCandlestickSeries({
            upColor: '#22c55e',
            downColor: '#ff3366',
            borderUpColor: '#16a34a',
            borderDownColor: '#ff0044',
            wickUpColor: '#22c55e',
            wickDownColor: '#ff3366'
        });
        
        this.chart.timeScale().fitContent();
        
        this.chart.timeScale().subscribeVisibleTimeRangeChange((timeRange) => {
            if (timeRange) {
                this.savedTimeRange = timeRange;
                this.chartManuallyAdjusted = true;
            }
        });
    }

    bindEvents() {
        document.getElementById('start-bot').addEventListener('click', () => {
            this.startBot();
        });

        document.getElementById('stop-bot').addEventListener('click', () => {
            this.stopBot();
        });

        document.getElementById('close-position').addEventListener('click', () => {
            this.closePosition();
        });

        document.getElementById('delete-trade').addEventListener('click', () => {
            this.deleteLastTrade();
        });

        document.getElementById('reset-balance').addEventListener('click', () => {
            this.resetBalance();
        });

        document.getElementById('open-short').addEventListener('click', () => {
            this.openShort();
        });

        // Timeframe buttons
        document.querySelectorAll('.tf-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                this.currentTimeframe = e.target.getAttribute('data-tf');
                this.chartManuallyAdjusted = false;
                this.savedTimeRange = null;
                this.updateChart();
            });
        });
    }

    async updateChart() {
        try {
            const response = await fetch(`/api/chart_data?timeframe=${this.currentTimeframe}`);
            if (!response.ok) return;
            
            const data = await response.json();
            
            if (!this.candlestickSeries || !data.candles || data.candles.length === 0) return;
            
            // Convert time strings to timestamps
            const candles = data.candles.map((candle, idx) => ({
                time: Math.floor(Date.now() / 1000) - (data.candles.length - idx) * 60,
                open: candle.open,
                high: candle.high,
                low: candle.low,
                close: candle.close
            }));
            
            this.candlestickSeries.setData(candles);
            
            // Add SAR points as markers with offset
            if (data.sar_points && data.sar_points.length > 0) {
                const markers = data.sar_points.map((point, idx) => ({
                    time: candles[idx].time,
                    position: point.trend === 'up' ? 'belowBar' : 'aboveBar',
                    color: point.color,
                    shape: 'circle',
                    size: 'large'
                }));
                this.candlestickSeries.setMarkers(markers);
            }
            
            if (!this.chartManuallyAdjusted) {
                this.chart.timeScale().fitContent();
            }
        } catch (error) {
            console.error('Chart update error:', error);
        }
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
                this.showNotification('success', data.message || 'Bot started successfully');
            } else {
                this.showNotification('error', data.error || 'Failed to start bot');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
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
                this.showNotification('success', data.message || 'Bot stopped successfully');
            } else {
                this.showNotification('error', data.error || 'Failed to stop bot');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
            console.error('Stop bot error:', error);
        }
    }

    async closePosition() {
        try {
            const response = await fetch('/api/close_position', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message || 'Position closed successfully');
            } else {
                this.showNotification('error', data.error || 'Failed to close position');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
            console.error('Close position error:', error);
        }
    }

    async deleteLastTrade() {
        try {
            const response = await fetch('/api/delete_last_trade', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message || 'Last trade deleted successfully');
                this.updateDashboard();
            } else {
                this.showNotification('error', data.error || 'Failed to delete last trade');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
            console.error('Delete trade error:', error);
        }
    }

    async resetBalance() {
        try {
            const response = await fetch('/api/reset_balance', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message || 'Balance reset successfully');
                this.updateDashboard();
            } else {
                this.showNotification('error', data.error || 'Failed to reset balance');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
            console.error('Reset balance error:', error);
        }
    }

    async openShort() {
        try {
            const response = await fetch('/api/open_short', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message || 'SHORT position opened successfully');
                this.updateDashboard();
            } else {
                this.showNotification('error', data.error || 'Failed to open SHORT position');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
            console.error('Open short error:', error);
        }
    }

    async updateDashboard() {
        if (this.isUpdating) return;
        this.isUpdating = true;

        try {
            // Fetch top pair price from gainers
            try {
                const gainersRes = await fetch('/api/top_gainers');
                if (gainersRes.ok) {
                    const gainersData = await gainersRes.json();
                    if (gainersData.gainers && gainersData.gainers.length > 0) {
                        const topPair = gainersData.gainers[0];
                        this.topPairPrice = parseFloat(topPair.price || 0);  // Store top 1 price
                        this.topPairSymbol = (topPair.symbol) ? topPair.symbol.split('_')[0] : 'TOP1';  // Store top 1 symbol
                        const priceElement = document.getElementById('current-price');
                        const symbolDisplay = document.getElementById('symbol-display');
                        if (priceElement) {
                            priceElement.textContent = `$${this.topPairPrice.toFixed(2)}`;
                        }
                        // Only update symbol if NOT locked by position
                        if (symbolDisplay && !this.positionSymbolLocked) {
                            symbolDisplay.textContent = this.topPairSymbol;
                        }
                        // Also update chart symbol
                        const chartSymbol = document.getElementById('chart-symbol');
                        if (chartSymbol && !this.positionSymbolLocked) {
                            chartSymbol.textContent = this.topPairSymbol;
                        }
                    }
                }
            } catch (err) {
                console.log('Top pair price error:', err);
            }

            const response = await fetch('/api/status');
            if (!response.ok) {
                console.error('Status fetch failed');
                return;
            }

            const data = await response.json();

            // Update current symbol
            if (data.current_symbol) {
                this.currentSymbol = data.current_symbol;
            }

            const statusBadge = document.getElementById('bot-status');
            if (data.bot_running) {
                statusBadge.textContent = 'RUNNING';
                statusBadge.className = 'badge bg-success';
            } else {
                statusBadge.textContent = 'STOPPED';
                statusBadge.className = 'badge bg-danger';
            }

            document.getElementById('balance').textContent = `$${parseFloat(data.balance).toFixed(2)}`;
            document.getElementById('available').textContent = `$${parseFloat(data.available).toFixed(2)}`;

            if (data.sar_directions) {
                this.updateSARDirections(data.sar_directions);
            }

            if (data.in_position && data.position) {
                document.getElementById('position-status').textContent = data.position.side.toUpperCase();
                // Lock symbol when position opens
                const symbolDisplay = document.getElementById('symbol-display');
                const chartSymbol = document.getElementById('chart-symbol');
                if (data.position.symbol) {
                    const positionSymbol = data.position.symbol.split('_')[0];  // Get symbol without _USDT
                    if (!this.positionSymbolLocked) {
                        // First time position opened - lock the symbol
                        this.positionSymbolLocked = true;
                        this.lockedSymbol = positionSymbol;
                    }
                    if (symbolDisplay) {
                        symbolDisplay.textContent = this.lockedSymbol;
                    }
                    if (chartSymbol) {
                        chartSymbol.textContent = this.lockedSymbol;
                    }
                }
                this.updatePosition(data.position, data.current_price, data);  // Use API current price
            } else {
                document.getElementById('position-status').textContent = 'No Position';
                // Unlock symbol when position closes
                this.positionSymbolLocked = false;
                this.lockedSymbol = null;
                this.clearPosition();
            }

            if (data.trades) {
                this.updateTrades(data.trades);
            }

            this.lastUpdateTime = new Date();
        } catch (error) {
            console.error('Dashboard update error:', error);
        } finally {
            this.isUpdating = false;
        }
    }

    updateSARDirections(directions) {
        const timeframes = ['1m', '5m', '15m', '30m'];
        let allMatch = true;
        let matchDirection = null;
        
        timeframes.forEach(tf => {
            const element = document.getElementById(`sar-${tf}`);
            const container = document.getElementById(`sar-${tf}-container`);
            const direction = directions[tf];
            
            if (element && container) {
                element.className = 'badge sar-badge';
                container.classList.remove('text-danger', 'text-success', 'text-warning');
                
                if (direction === 'long') {
                    element.textContent = 'LONG';
                    element.classList.add('bg-success');
                    container.classList.add('text-success');
                    if (matchDirection === null) {
                        matchDirection = 'long';
                    } else if (matchDirection !== 'long') {
                        allMatch = false;
                    }
                } else if (direction === 'short') {
                    element.textContent = 'SHORT';
                    element.classList.add('bg-danger');
                    container.classList.add('text-danger');
                    container.classList.add('text-danger');
                    if (matchDirection === null) {
                        matchDirection = 'short';
                    } else if (matchDirection !== 'short') {
                        allMatch = false;
                    }
                } else {
                    element.textContent = 'N/A';
                    element.classList.add('bg-secondary');
                    container.classList.remove('text-success', 'text-danger');
                    allMatch = false;
                }
            }
        });
        
        const signalElement = document.getElementById('signal-status');
        if (signalElement) {
            if (allMatch && matchDirection) {
                if (matchDirection === 'long') {
                    signalElement.textContent = 'LONG SIGNAL';
                    signalElement.className = 'badge bg-success signal-badge';
                } else {
                    signalElement.textContent = 'SHORT SIGNAL';
                    signalElement.className = 'badge bg-danger signal-badge';
                }
            } else {
                signalElement.textContent = 'NO SIGNAL';
                signalElement.className = 'badge bg-secondary signal-badge';
            }
        }
    }

    updatePosition(position, currentPrice, statusData) {
        const noPosition = document.getElementById('no-position');
        const currentPosition = document.getElementById('current-position');
        
        if (noPosition) noPosition.classList.add('d-none');
        if (currentPosition) currentPosition.classList.remove('d-none');
        
        // Display asset symbol
        const symbolElement = document.getElementById('pos-symbol');
        if (symbolElement) {
            const symbol = position.symbol || this.currentSymbol || 'UNKNOWN';
            symbolElement.textContent = symbol;
        }
        
        const sideBadge = document.getElementById('pos-side');
        if (sideBadge) {
            sideBadge.textContent = position.side.toUpperCase();
            sideBadge.className = position.side === 'long' ? 'badge bg-success' : 'badge bg-danger';
        }
        
        const colorClass = position.side === 'long' ? 'text-success' : 'text-danger';
        const symbolBase = this.currentSymbol ? this.currentSymbol.split('/')[0] : 'TOP1';
        
        // Display entry price
        const entryPriceElement = document.getElementById('pos-entry-price');
        if (entryPriceElement) {
            const entryPrice = parseFloat(position.entry_price || 0);
            entryPriceElement.textContent = `$${entryPrice.toFixed(6)}`;
            entryPriceElement.className = colorClass;
        }
        
        const entryElement = document.getElementById('pos-entry');
        if (entryElement) {
            entryElement.textContent = `$${parseFloat(currentPrice).toFixed(6)}`;
            entryElement.className = colorClass;
        }
        
        const sizeElement = document.getElementById('pos-size');
        if (sizeElement) {
            const top1Display = (statusData && statusData.top1_display) || position.top1_display || 'N/A';
            sizeElement.textContent = top1Display;
            sizeElement.className = colorClass;
        }
        
        const notionalElement = document.getElementById('pos-notional');
        if (notionalElement) {
            const notional = parseFloat(position.notional || 0);
            notionalElement.textContent = `$${notional.toFixed(2)}`;
            notionalElement.className = colorClass;
        }
        
        const timeElement = document.getElementById('pos-time');
        if (timeElement && position.entry_time) {
            const entryTime = new Date(position.entry_time);
            timeElement.textContent = entryTime.toLocaleTimeString();
            timeElement.className = colorClass;
        }
        
        const pnlElement = document.getElementById('pos-pnl');
        if (pnlElement) {
            // Use unrealized_pnl from API (already calculated correctly with leverage)
            const pnl = parseFloat(position.unrealized_pnl || 0);
            
            const pnlSign = pnl >= 0 ? '+' : '';
            pnlElement.textContent = `${pnlSign}$${pnl.toFixed(2)}`;
            pnlElement.className = pnl >= 0 ? 'text-success' : 'text-danger';
        }
    }

    clearPosition() {
        const noPosition = document.getElementById('no-position');
        const currentPosition = document.getElementById('current-position');
        
        if (noPosition) noPosition.classList.remove('d-none');
        if (currentPosition) currentPosition.classList.add('d-none');
    }

    updateTrades(trades) {
        const container = document.getElementById('trades-container');
        if (!container) return;
        
        if (!trades || trades.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i class="fas fa-clock fa-2x mb-3"></i>
                    <p>No completed trades</p>
                </div>
            `;
            return;
        }
        
        const reversedTrades = [...trades].reverse();
        
        container.innerHTML = reversedTrades.map(trade => {
            const pnl = parseFloat(trade.pnl);
            const pnlClass = pnl >= 0 ? 'trade-profit' : 'trade-loss';
            const pnlSign = pnl >= 0 ? '+' : '';
            const sideClass = trade.side === 'long' ? 'bg-success' : 'bg-danger';
            
            return `
                <div class="trade-item">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <span class="badge ${sideClass} me-2">${trade.side.toUpperCase()}</span>
                            <small class="text-muted">${trade.duration || 'N/A'}</small>
                        </div>
                        <div class="${pnlClass}">
                            ${pnlSign}$${pnl.toFixed(2)}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    showNotification(type, message) {
        const notification = document.createElement('div');
        notification.className = `alert alert-${type === 'success' ? 'success' : 'danger'} position-fixed`;
        notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; max-width: 300px;';
        notification.textContent = message;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.remove();
        }, 3000);
    }

    updateTopGainers() {
        const container = document.getElementById('top-gainers-list');
        const header = document.querySelector('[data-gainers-header]');
        
        fetch('/api/top_gainers')
            .then(res => res.json())
            .then(data => {
                // Обновляем заголовок
                if (header) {
                    const total = data.total_pairs || 0;
                    const status = data.cached ? '✅' : '⏳';
                    header.textContent = `${status} GATE - ${total} futures pairs`;
                }
                
                if (!data.gainers || data.gainers.length === 0) {
                    container.innerHTML = '<div class="text-center text-muted p-3">⏳ Loading futures data...</div>';
                    return;
                }
                
                const html = data.gainers.map((coin, idx) => {
                    const changeClass = coin.change >= 0 ? 'text-success' : 'text-danger';
                    const changeSign = coin.change >= 0 ? '+' : '';
                    const geckoRank = coin.gecko_rank !== 'N/A' ? `#${coin.gecko_rank}` : 'N/A';
                    return `
                        <div class="d-flex justify-content-between align-items-center p-2 border-bottom" style="font-size: 0.9rem;">
                            <div>
                                <span class="badge bg-primary me-2" style="font-size: 0.75rem;">${idx + 1}</span>
                                <strong>${coin.symbol}</strong>
                                <span class="badge bg-warning text-dark ms-2" style="font-size: 0.7rem;">CG: ${geckoRank}</span>
                            </div>
                            <div class="text-end">
                                <div class="text-light" style="font-size: 0.85rem;">$${coin.price ? coin.price.toFixed(6) : 'N/A'}</div>
                                <div class="${changeClass}"><strong style="font-size: 0.85rem;">${changeSign}${coin.change.toFixed(2)}%</strong></div>
                            </div>
                        </div>
                    `;
                }).join('');
                container.innerHTML = html;
            })
            .catch(err => {
                console.log('Top gainers error:', err);
                if (container) container.innerHTML = '<div class="text-warning p-3">⚠️ Loading Gate.io data...</div>';
            });
    }

    startDataUpdates() {
        setInterval(() => this.updateDashboard(), 3000);
        setInterval(() => this.updateChart(), 5000);
        setInterval(() => this.updateTopGainers(), 15000);
        this.updateTopGainers();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new TradingDashboard();
});

// Update current trading symbol display
async function updateCurrentSymbol() {
    try {
        const res = await fetch('/api/current_trading_symbol');
        if (!res.ok) return;
        const data = await res.json();
        const symbol = data.symbol || 'TOP1/USDT';
        
        // Update all symbol displays
        document.getElementById('header-symbol').textContent = symbol;
        document.getElementById('chart-symbol').textContent = symbol;
    } catch (err) {
        console.log('Symbol update error:', err);
    }
}

// Call on init and every 15 seconds
document.addEventListener('DOMContentLoaded', updateCurrentSymbol);
setInterval(updateCurrentSymbol, 15000);
