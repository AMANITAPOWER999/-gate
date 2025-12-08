class TradingDashboard {
    constructor() {
        this.lastUpdateTime = null;
        this.isUpdating = false;
        this.currentTimeframe = '5m';
        this.chart = null;
        this.candlestickSeries = null;
        this.sarSeries = null;
        this.entryMarkerSeries = null;
        this.chartManuallyAdjusted = false;
        this.savedTimeRange = null;
        this.currentSymbol = 'TOP1/USDT';
        this.topPairPrice = 0;
        this.positionSymbolLocked = false;
        this.lockedSymbol = null;
        this.openLevels = new Set();
        this.closeLevels = new Set();
        this.apiConnected = false;
        this.lastPosition = null;
        this.rebalanceActive = false;
        
        this.initChart();
        this.loadStrategyConfig();
        this.loadLeverage();
        this.bindEvents();
        this.checkSavedAPIConnection();
        this.loadTradingMode();
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
            upColor: '#10b981',
            downColor: '#ef4444',
            borderUpColor: '#059669',
            borderDownColor: '#dc2626',
            wickUpColor: '#10b981',
            wickDownColor: '#ef4444'
        });
        
        this.sarSeries = this.chart.addLineSeries({
            color: 'rgba(255, 255, 255, 0.5)',
            lineWidth: 1,
            pointMarkersVisible: true
        });
        
        this.entryMarkerSeries = this.chart.addLineSeries({
            color: 'rgba(59, 130, 246, 0.8)',
            lineWidth: 2,
            lineStyle: 2,
            pointMarkersVisible: false
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
        // Helper to safely bind events (check if element exists)
        const bindIfExists = (id, event, fn) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener(event, fn);
        };

        bindIfExists('api-status-indicator', 'click', () => this.disconnectAPI());
        bindIfExists('start-bot', 'click', () => this.startBot());
        bindIfExists('stop-bot', 'click', () => this.stopBot());
        bindIfExists('close-position', 'click', () => this.closePosition());
        bindIfExists('delete-trade', 'click', () => this.deleteLastTrade());
        bindIfExists('reset-balance', 'click', () => this.resetBalance());
        bindIfExists('open-long', 'click', () => this.openLong());
        bindIfExists('open-short', 'click', () => this.openShort());

        // API Modal
        bindIfExists('connect-api-btn', 'click', () => {
            const modal = document.getElementById('api-modal');
            if (modal) modal.classList.add('show');
        });

        bindIfExists('close-api-modal', 'click', () => {
            const modal = document.getElementById('api-modal');
            if (modal) modal.classList.remove('show');
        });

        const apiModal = document.getElementById('api-modal');
        if (apiModal) {
            apiModal.addEventListener('click', (e) => {
                if (e.target === apiModal) apiModal.classList.remove('show');
            });
        }

        const apiForm = document.getElementById('api-form-modal');
        if (apiForm) {
            apiForm.addEventListener('submit', (e) => {
                e.preventDefault();
                const uid = document.getElementById('modal-uid').value;
                const apiKey = document.getElementById('modal-api-key').value;
                const apiSecret = document.getElementById('modal-api-secret').value;
                this.submitApiConnection(uid, apiKey, apiSecret);
            });
        }

        // Verify UID on blur (if element exists)
        const uidInput = document.getElementById('modal-uid');
        if (uidInput) {
            uidInput.addEventListener('blur', () => {
                const uid = document.getElementById('modal-uid').value;
                if (uid) {
                    this.verifyReferral(uid);
                }
            });
        }

        // Balance rebalance button (only exists when API connected)
        const rebalanceBtn = document.getElementById('balance-rebalance-btn');
        if (rebalanceBtn) {
            rebalanceBtn.addEventListener('click', () => {
                this.toggleRebalance();
            });
        }

        // Leverage buttons
        document.querySelectorAll('.leverage-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.leverage-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                const leverage = e.target.getAttribute('data-leverage');
                this.setLeverage(leverage);
            });
        });

        // DEMO/REAL mode toggle buttons
        document.querySelectorAll('.btn-mode-toggle').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const mode = e.currentTarget.getAttribute('data-mode');
                this.setTradingMode(mode);
            });
        });

        // Level open buttons
        document.querySelectorAll('.level-open-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const level = e.currentTarget.getAttribute('data-level');
                this.toggleOpenLevel(level, e.currentTarget);
            });
        });

        // Level close buttons
        document.querySelectorAll('.level-close-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const level = e.currentTarget.getAttribute('data-level');
                this.toggleCloseLevel(level, e.currentTarget);
            });
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

    async loadStrategyConfig() {
        try {
            const response = await fetch('/api/get_strategy_config');
            if (response.ok) {
                const data = await response.json();
                this.openLevels = new Set(data.open_levels || []);
                this.closeLevels = new Set(data.close_levels || []);
                this.updateLevelButtons();
            }
        } catch (error) {
            console.error('Load strategy error:', error);
        }
    }

    updateLevelButtons() {
        document.querySelectorAll('.level-open-btn').forEach(btn => {
            const level = btn.getAttribute('data-level');
            btn.classList.toggle('active', this.openLevels.has(level));
        });
        document.querySelectorAll('.level-close-btn').forEach(btn => {
            const level = btn.getAttribute('data-level');
            btn.classList.toggle('active', this.closeLevels.has(level));
        });
    }

    async toggleOpenLevel(level, button) {
        if (this.openLevels.has(level)) {
            this.openLevels.delete(level);
            button.classList.remove('active');
        } else {
            this.openLevels.add(level);
            button.classList.add('active');
        }
        await this.saveStrategyConfig();
    }

    async toggleCloseLevel(level, button) {
        if (this.closeLevels.has(level)) {
            this.closeLevels.delete(level);
            button.classList.remove('active');
        } else {
            this.closeLevels.add(level);
            button.classList.add('active');
        }
        await this.saveStrategyConfig();
    }

    async saveStrategyConfig() {
        try {
            const response = await fetch('/api/set_strategy_config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    open_levels: Array.from(this.openLevels),
                    close_levels: Array.from(this.closeLevels)
                })
            });

            if (!response.ok) {
                console.error('Failed to save strategy config');
            }
        } catch (error) {
            console.error('Save strategy error:', error);
        }
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
            
            // Add SAR points as separate series for better visibility
            if (data.sar_points && data.sar_points.length > 0 && this.sarSeries) {
                const sarData = data.sar_points
                    .filter((point, idx) => idx < candles.length)
                    .map((point, idx) => ({
                        time: candles[idx].time,
                        value: point.value
                    }));
                if (sarData.length > 0) {
                    this.sarSeries.setData(sarData);
                }
                
                // Add colored markers for SAR points
                const markers = data.sar_points.map((point, idx) => ({
                    time: candles[idx].time,
                    position: point.trend === 'up' ? 'belowBar' : 'aboveBar',
                    color: point.color,
                    shape: 'circle',
                    size: 'small',
                    text: ''
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
                this.showNotification('success', data.message || 'Balance reset to $100');
                this.updateDashboard();
            } else {
                this.showNotification('error', data.error || 'Failed to reset balance');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
            console.error('Reset balance error:', error);
        }
    }

    async openLong() {
        // ✅ Removed API check - allow opening positions in virtual mode
        try {
            const response = await fetch('/api/open_long', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', data.message || 'LONG position opened successfully');
                this.updateDashboard();
            } else {
                this.showNotification('error', data.error || 'Failed to open LONG position');
            }
        } catch (error) {
            this.showNotification('error', 'Server connection error');
            console.error('Open long error:', error);
        }
    }

    async openShort() {
        // ✅ Removed API check - allow opening positions in virtual mode
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
                            const decimals = this.topPairPrice < 0.01 ? 6 : (this.topPairPrice < 1 ? 4 : 2);
                            priceElement.textContent = `$${this.topPairPrice.toFixed(decimals)}`;
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

            // SYNC API connection status from backend
            if (data.api_connected !== undefined) {
                this.apiConnected = data.api_connected;
            }
            
            // ✅ SYNC trading mode from backend
            if (data.trading_mode) {
                this.updateModeButtons(data.trading_mode);
            }

            // Update current symbol - show TOP1 when no position, locked symbol when in position
            if (data.in_position) {
                // Show LOCKED symbol (current trading coin) when position is open
                if (this.lockedSymbol) {
                    this.currentSymbol = this.lockedSymbol;
                }
            } else {
                // Show TOP1 when position is closed
                if (this.topPairSymbol) {
                    this.currentSymbol = this.topPairSymbol;
                } else if (data.current_symbol) {
                    this.currentSymbol = data.current_symbol;
                }
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
        const timeframes = ['1m', '5m', '15m', '30m', '1h', '60m'];
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
        // Store position for pair locking
        this.lastPosition = position;
        
        const noPosition = document.getElementById('no-position');
        const currentPosition = document.getElementById('current-position');
        
        if (noPosition) noPosition.classList.add('d-none');
        if (currentPosition) currentPosition.classList.remove('d-none');
        
        // Add entry marker on chart
        if (position && position.entry_time && this.entryMarkerSeries) {
            const entryDate = new Date(position.entry_time);
            const entryTime = Math.floor(entryDate.getTime() / 1000);
            const entryPrice = parseFloat(position.entry_price || 0);
            
            const markerData = [
                { time: entryTime, value: entryPrice }
            ];
            this.entryMarkerSeries.setData(markerData);
        }
        
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
        
        // Display entry price - FIXED at position open (NOT updated on TOP1 symbol change)
        const entryPriceElement = document.getElementById('pos-entry-price');
        if (entryPriceElement) {
            const entryPrice = parseFloat(position.entry_price || 0);
            entryPriceElement.textContent = `$${entryPrice.toFixed(6)}`;
            entryPriceElement.className = colorClass;
        }
        
        // Display current price of TOP1 pair - UPDATES live (different from Entry Price)
        const entryElement = document.getElementById('pos-entry');
        if (entryElement) {
            entryElement.textContent = `$${parseFloat(currentPrice).toFixed(6)}`;
            entryElement.className = colorClass;
        }
        
        // Display TOP1 pair name
        const sizeElement = document.getElementById('pos-size');
        if (sizeElement) {
            const top1Display = (statusData && statusData.top1_display) || position.top1_display || 'N/A';
            sizeElement.textContent = top1Display;
            sizeElement.className = colorClass;
        }
        
        // Display notional value - FIXED at position open (NOT updated on TOP1 symbol change)
        const notionalElement = document.getElementById('pos-notional');
        if (notionalElement) {
            const notional = parseFloat(position.notional || 0);
            notionalElement.textContent = `$${notional.toFixed(2)}`;
            notionalElement.className = colorClass;
        }
        
        const timeElement = document.getElementById('pos-time');
        if (timeElement) {
            // Use open_timestamp from Gate.io API, or entry_time from DEMO mode
            let openTimestamp = position.open_timestamp || 0;
            let entryTime = position.entry_time || null;
            
            if (openTimestamp > 0) {
                this.positionStartTime = new Date(openTimestamp);
            } else if (entryTime) {
                // DEMO mode - use entry_time (ISO string in UTC)
                // Add 'Z' suffix if not present to indicate UTC timezone
                if (!entryTime.endsWith('Z') && !entryTime.includes('+')) {
                    entryTime = entryTime + 'Z';
                }
                this.positionStartTime = new Date(entryTime);
            }
            
            if (this.positionStartTime) {
                this.updatePositionTimer(timeElement, colorClass);
                // Start interval only once
                if (!this.positionTimerInterval) {
                    this.startPositionTimer(timeElement, colorClass);
                }
            } else {
                timeElement.textContent = 'N/A';
            }
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

    updatePositionTimer(timeElement, colorClass) {
        if (!this.positionStartTime || !timeElement) return;
        const now = new Date();
        const elapsed = Math.floor((now - this.positionStartTime) / 1000);
        const hours = Math.floor(elapsed / 3600);
        const minutes = Math.floor((elapsed % 3600) / 60);
        const seconds = elapsed % 60;
        if (hours > 0) {
            timeElement.textContent = `${hours}ч ${minutes}м ${seconds}с`;
        } else {
            timeElement.textContent = `${minutes}м ${seconds}с`;
        }
        timeElement.className = colorClass;
    }

    startPositionTimer(timeElement, colorClass) {
        if (this.positionTimerInterval) clearInterval(this.positionTimerInterval);
        this.positionTimerInterval = setInterval(() => {
            this.updatePositionTimer(timeElement, colorClass);
        }, 1000);
    }

    clearPosition() {
        const noPosition = document.getElementById('no-position');
        const currentPosition = document.getElementById('current-position');
        
        if (this.positionTimerInterval) {
            clearInterval(this.positionTimerInterval);
            this.positionTimerInterval = null;
        }
        this.positionStartTime = null;
        this.lastPositionId = null;
        
        // Remove entry marker from chart
        if (this.entryMarkerSeries) {
            this.entryMarkerSeries.setData([]);
        }
        
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
            const symbol = trade.symbol || 'N/A';
            
            return `
                <div class="trade-item">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <span class="badge ${sideClass} me-2">${trade.side.toUpperCase()}</span>
                            <strong class="me-2">${symbol}</strong>
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

    updateOnlineUsers() {
        fetch('/api/online_users')
            .then(res => res.json())
            .then(data => {
                const onlineEl = document.getElementById('online-users');
                if (onlineEl) {
                    onlineEl.textContent = data.online_users || 0;
                }
                // Update modal list if open
                const listEl = document.getElementById('online-users-list');
                if (listEl && data.users) {
                    if (data.users.length === 0) {
                        listEl.innerHTML = '<div class="text-muted">No users online</div>';
                    } else {
                        listEl.innerHTML = data.users.map((user, idx) => `
                            <div class="d-flex justify-content-between align-items-center p-2 border-bottom border-secondary">
                                <div>
                                    <span class="badge bg-success me-2">${idx + 1}</span>
                                    <i class="fas fa-user text-info me-2"></i>
                                    <span class="text-light">${user.id}</span>
                                </div>
                                <div class="text-end">
                                    <small class="text-muted d-block">${user.ip}</small>
                                    <small class="text-success">${user.last_seen}</small>
                                </div>
                            </div>
                        `).join('');
                    }
                }
            })
            .catch(err => console.debug('Online users error:', err));
    }

    sendHeartbeat() {
        fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
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
                
                // Get locked symbol from position if exists
                let lockedSymbol = null;
                if (this.lastPosition && this.lastPosition.symbol) {
                    lockedSymbol = this.lastPosition.symbol;
                }
                
                const html = data.gainers.map((coin, idx) => {
                    const changeClass = coin.change >= 0 ? 'text-success' : 'text-danger';
                    const changeSign = coin.change >= 0 ? '+' : '';
                    const geckoRank = coin.gecko_rank !== 'N/A' ? `#${coin.gecko_rank}` : 'N/A';
                    const geckoDisplay = coin.gecko_rank !== 'N/A' ? `<span class="badge bg-warning text-dark ms-2" style="font-size: 0.7rem;">CG: ${geckoRank}</span>` : '';
                    
                    // Check if this pair is locked
                    const isLocked = lockedSymbol && coin.symbol === lockedSymbol;
                    const lockedClass = isLocked ? 'pair-locked' : '';
                    const lockIcon = isLocked ? '<i class="fas fa-lock lock-icon me-2"></i>' : '';
                    const lockedText = isLocked ? '<small class="text-muted d-block">locked during trade</small>' : '';
                    
                    return `
                        <div class="d-flex justify-content-between align-items-center p-2 border-bottom ${lockedClass}" style="font-size: 0.9rem;">
                            <div>
                                <span class="badge bg-primary me-2" style="font-size: 0.75rem;">${idx + 1}</span>
                                ${lockIcon}
                                <strong class="${changeClass}">${coin.symbol}</strong>
                                ${geckoDisplay}
                                ${lockedText}
                            </div>
                            <div class="text-end">
                                <div class="${changeClass}" style="font-size: 0.85rem;">$${coin.price ? coin.price.toFixed(6) : 'N/A'}</div>
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

    async loadLeverage() {
        try {
            const response = await fetch('/api/get_leverage');
            if (response.ok) {
                const data = await response.json();
                const leverage = data.leverage;
                document.querySelectorAll('.leverage-btn').forEach(btn => {
                    btn.classList.remove('active');
                    if (parseInt(btn.getAttribute('data-leverage')) === leverage) {
                        btn.classList.add('active');
                    }
                });
            }
        } catch (error) {
            console.error('Load leverage error:', error);
        }
    }

    async verifyReferral(uid) {
        try {
            const response = await fetch('/api/verify_referral', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: uid })
            });

            const data = await response.json();
            const statusDiv = document.getElementById('referral-check-status');

            if (response.ok && data.verified) {
                statusDiv.innerHTML = '<small><i class="fas fa-check text-success"></i> ✓ Referral verified!</small>';
                statusDiv.className = 'alert alert-success alert-sm mb-2';
                statusDiv.style.display = 'block';
                return true;
            } else {
                statusDiv.innerHTML = '<small><i class="fas fa-times text-danger"></i> ✗ Must register via referral link first</small>';
                statusDiv.className = 'alert alert-danger alert-sm mb-2';
                statusDiv.style.display = 'block';
                return false;
            }
        } catch (error) {
            console.error('Referral verification error:', error);
            return false;
        }
    }

    async submitApiConnection(uid, apiKey, apiSecret) {
        try {
            const btn = document.querySelector('#api-form-modal button[type="submit"]');
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Connecting...';

            console.log('🔐 Sending API connection request:', { uid, api_key: apiKey.slice(0,5) + '...', referral_verified: true });

            const response = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    uid: uid,
                    api_key: apiKey,
                    api_secret: apiSecret,
                    referral_verified: true
                })
            });

            console.log('🔐 API login response status:', response.status);

            const data = await response.json();

            if (response.ok) {
                // Save API connection data to localStorage
                const apiData = {
                    uid: uid,
                    api_key: apiKey,
                    api_secret: apiSecret,
                    connected: true,
                    connectedAt: new Date().toISOString()
                };
                localStorage.setItem('gateio_api_data', JSON.stringify(apiData));
                
                // Show success message
                const alertHtml = `<div class="alert alert-success alert-dismissible fade show">
                    <strong>✅ Connected to Gate.io!</strong><br>
                    <small>Real Balance: $${data.balance || 0}<br>Mode: ${data.trading_mode || 'LIVE'}</small>
                    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                </div>`;
                document.getElementById('api-alert-container').innerHTML = alertHtml;
                
                // Show API connected indicator (green)
                this.showAPIConnectedIndicator();
                
                this.showNotification('success', '✅ API Connected! Closing paper positions...');
                
                // Close any open positions
                try {
                    await fetch('/api/close_position', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });
                    this.showNotification('success', '✅ Paper positions closed! Real trading active!');
                } catch (e) {
                    console.log('No positions to close or error:', e);
                }
                
                setTimeout(() => {
                    document.getElementById('api-modal').classList.remove('show');
                    document.getElementById('api-form-modal').reset();
                    document.getElementById('referral-check-status').style.display = 'none';
                    this.updateDashboard();
                }, 1500);
            } else {
                const alertHtml = `<div class="alert alert-danger alert-dismissible fade show"><strong>Error:</strong> ${data.error || 'Connection failed'}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>`;
                document.getElementById('api-alert-container').innerHTML = alertHtml;
            }
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-plug"></i> Connect & Verify';
        } catch (error) {
            console.error('API connection error:', error);
            this.showNotification('error', 'Error: ' + error.message);
        }
    }

    async setLeverage(leverage) {
        try {
            const response = await fetch('/api/set_leverage', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ leverage: parseInt(leverage) })
            });

            const data = await response.json();
            
            if (response.ok) {
                this.showNotification('success', `Leverage set to ${leverage}x`);
            } else {
                this.showNotification('error', data.error || 'Failed to set leverage');
            }
        } catch (error) {
            console.error('Leverage error:', error);
            this.showNotification('error', 'Error setting leverage');
        }
    }

    async setTradingMode(mode) {
        try {
            // ✅ STOP auto-updates to prevent race condition
            if (this.dataUpdateInterval) {
                clearInterval(this.dataUpdateInterval);
                this.dataUpdateInterval = null;
            }
            
            const response = await fetch('/api/set_trading_mode', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ mode: mode })
            });

            const data = await response.json();
            
            if (response.ok) {
                // ✅ IMMEDIATELY update UI with new balance from response
                if (data.balance !== undefined) {
                    const balanceElement = document.getElementById('balance');
                    if (balanceElement) {
                        balanceElement.textContent = `$${data.balance.toFixed(2)}`;
                        console.log(`✅ Balance updated immediately: $${data.balance.toFixed(2)}`);
                    }
                }
                
                this.updateModeButtons(mode);
                const modeText = mode === 'demo' ? 'DEMO ($100)' : 'REAL (Gate.io)';
                this.showNotification('success', `Режим: ${modeText}`);
                
                // ✅ Force immediate update after mode switch (prevent race condition)
                await new Promise(r => setTimeout(r, 300)); // Wait for backend sync
                await this.updateDashboard(); // Update immediately after
                
                // ✅ Resume auto-update after fixed delay
                await new Promise(r => setTimeout(r, 500));
                this.startAutoUpdate(); // Restart auto-update
            } else {
                this.showNotification('error', data.error || 'Ошибка переключения режима');
                // Resume auto-update on error
                this.startAutoUpdate();
            }
        } catch (error) {
            console.error('Mode switch error:', error);
            this.showNotification('error', 'Ошибка переключения режима');
            // Resume auto-update on error
            this.startAutoUpdate();
        }
    }
    
    startAutoUpdate() {
        // ✅ Start auto-update with 2s interval
        if (!this.dataUpdateInterval) {
            this.dataUpdateInterval = setInterval(() => {
                this.updateDashboard();
            }, 2000);
        }
    }

    updateModeButtons(mode) {
        document.querySelectorAll('.btn-mode-toggle').forEach(btn => {
            btn.classList.remove('active');
            if (btn.getAttribute('data-mode') === mode) {
                btn.classList.add('active');
            }
        });
    }

    async loadTradingMode() {
        try {
            const response = await fetch('/api/get_trading_mode');
            if (response.ok) {
                const data = await response.json();
                this.updateModeButtons(data.mode);
            }
        } catch (error) {
            console.error('Load trading mode error:', error);
        }
    }

    async toggleRebalance() {
        try {
            const btn = document.getElementById('balance-rebalance-btn');
            const status = document.getElementById('rebalance-status');
            const indicator = document.getElementById('rebalance-indicator');
            
            // Переключаем состояние
            this.rebalanceActive = !this.rebalanceActive;
            
            const response = await fetch('/api/toggle_rebalance', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ enabled: this.rebalanceActive })
            });

            const data = await response.json();
            
            if (response.ok) {
                // Обновляем UI в зависимости от состояния сервера
                this.rebalanceActive = data.rebalance_enabled;
                
                if (data.rebalance_enabled) {
                    status.textContent = 'ACTIVE';
                    indicator.style.color = '#10b981';
                } else {
                    status.textContent = 'Auto 20/80';
                    indicator.style.color = '#999';
                }
                this.showNotification('success', `Balance rebalance ${data.rebalance_enabled ? 'ENABLED' : 'DISABLED'}. Maintains 20% futures / 80% spot.`);
            } else {
                // Откатываем локальное состояние при ошибке
                this.rebalanceActive = !this.rebalanceActive;
                this.showNotification('error', data.error || 'Failed to toggle rebalance');
            }
        } catch (error) {
            console.error('Rebalance toggle error:', error);
            this.rebalanceActive = !this.rebalanceActive;
            this.showNotification('error', 'Error toggling rebalance');
        }
    }

    async checkSavedAPIConnection() {
        try {
            // ✅ FIRST: Check current trading mode from server
            let currentMode = 'demo';
            try {
                const modeResponse = await fetch('/api/get_trading_mode');
                if (modeResponse.ok) {
                    const modeData = await modeResponse.json();
                    currentMode = modeData.mode || 'demo';
                }
            } catch (e) {
                console.log('Could not fetch trading mode:', e);
                currentMode = 'demo';
            }
            
            // ✅ ALWAYS enable buttons in DEMO mode
            if (currentMode === 'demo') {
                this.enableDEMOButtons();
                return;
            }
            
            // ✅ In REAL mode, check API connection
            const savedData = localStorage.getItem('gateio_api_data');
            if (savedData) {
                const data = JSON.parse(savedData);
                if (data.connected) {
                    this.showAPIConnectedIndicator();
                    
                    // Fetch fresh balance from server instead of using cached value
                    try {
                        const response = await fetch('/api/status');
                        if (response.ok) {
                            const status = await response.json();
                            if (status.balance !== undefined) {
                                // Update balance display with fresh server value
                                const balanceEl = document.getElementById('balance-display');
                                if (balanceEl) {
                                    balanceEl.textContent = '$' + parseFloat(status.balance).toFixed(2);
                                }
                            }
                        }
                    } catch (e) {
                        console.log('Could not fetch fresh balance:', e);
                    }
                }
            } else {
                // Fallback: enable DEMO buttons if no API data
                this.enableDEMOButtons();
            }
        } catch (e) {
            console.log('Error loading saved API data:', e);
            // Fallback: enable DEMO buttons on error
            this.enableDEMOButtons();
        }
    }
    
    enableDEMOButtons() {
        // ✅ Enable OPEN LONG / OPEN SHORT buttons in DEMO mode
        const openLongBtn = document.getElementById('open-long');
        const openShortBtn = document.getElementById('open-short');
        if (openLongBtn) {
            openLongBtn.disabled = false;
            openLongBtn.style.opacity = '1';
            openLongBtn.style.cursor = 'pointer';
        }
        if (openShortBtn) {
            openShortBtn.disabled = false;
            openShortBtn.style.opacity = '1';
            openShortBtn.style.cursor = 'pointer';
        }
        // ✅ Disable REAL mode button when API not connected
        this.setREALButtonState(false);
    }
    
    setREALButtonState(enabled) {
        // ✅ Enable/disable REAL button based on API connection
        const realBtn = document.getElementById('mode-real');
        if (realBtn) {
            realBtn.disabled = !enabled;
            realBtn.style.opacity = enabled ? '1' : '0.5';
            realBtn.style.cursor = enabled ? 'pointer' : 'not-allowed';
            if (!enabled) {
                realBtn.title = 'Connect API first';
            } else {
                realBtn.title = '';
            }
        }
    }

    showAPIConnectedIndicator() {
        this.apiConnected = true;
        const indicator = document.getElementById('api-status-indicator');
        const statusText = document.getElementById('api-status-text');
        indicator.style.display = 'inline-flex';
        indicator.style.background = '#3b82f6';
        statusText.textContent = 'Connected';
        
        // Show and enable rebalance button
        const rebalanceBtn = document.getElementById('balance-rebalance-btn');
        if (rebalanceBtn) {
            rebalanceBtn.style.display = 'inline-flex';
            rebalanceBtn.disabled = false;
            rebalanceBtn.style.opacity = '1';
            rebalanceBtn.style.cursor = 'pointer';
        }
        
        // Disable manual open buttons when API is connected
        const openLongBtn = document.getElementById('open-long');
        const openShortBtn = document.getElementById('open-short');
        if (openLongBtn) {
            openLongBtn.disabled = true;
            openLongBtn.style.opacity = '0.5';
            openLongBtn.style.cursor = 'not-allowed';
        }
        if (openShortBtn) {
            openShortBtn.disabled = true;
            openShortBtn.style.opacity = '0.5';
            openShortBtn.style.cursor = 'not-allowed';
        }
        
        // Disable reset balance button when API is connected
        const resetBalanceBtn = document.getElementById('reset-balance');
        if (resetBalanceBtn) {
            resetBalanceBtn.disabled = true;
            resetBalanceBtn.style.opacity = '0.5';
            resetBalanceBtn.style.cursor = 'not-allowed';
            resetBalanceBtn.title = 'Reset disabled in LIVE mode';
        }
        
        // ✅ Enable REAL mode button when API is connected
        this.setREALButtonState(true);
    }

    async disconnectAPI() {
        if (confirm('🔌 Disconnect from real trading and return to paper mode?')) {
            try {
                // CALL LOGOUT endpoint to reset balance on SERVER
                const response = await fetch('/logout', { method: 'POST' });
                const data = await response.json();
                
                console.log('Disconnect response:', data);
                
                localStorage.removeItem('gateio_api_data');
                this.apiConnected = false;
                const indicator = document.getElementById('api-status-indicator');
                indicator.style.display = 'none';
                
                // Re-enable manual open buttons
                const openLongBtn = document.getElementById('open-long');
                const openShortBtn = document.getElementById('open-short');
                if (openLongBtn) {
                    openLongBtn.disabled = false;
                    openLongBtn.style.opacity = '1';
                    openLongBtn.style.cursor = 'pointer';
                }
                if (openShortBtn) {
                    openShortBtn.disabled = false;
                    openShortBtn.style.opacity = '1';
                    openShortBtn.style.cursor = 'pointer';
                }
                
                // ✅ Disable REAL button again after disconnect
                this.setREALButtonState(false);
                
                this.showNotification('success', `✅ Disconnected! Restored paper mode (balance: $${data.balance})`);
                setTimeout(() => {
                    location.reload();
                }, 1500);
            } catch (e) {
                console.error('Disconnect error:', e);
                this.showNotification('error', 'Error disconnecting from API');
            }
        }
    }

    startDataUpdates() {
        setInterval(() => this.updateDashboard(), 5000);
        setInterval(() => this.updateChart(), 5000);
        setInterval(() => this.updateTopGainers(), 15000);
        setInterval(() => this.updateOnlineUsers(), 5000);
        setInterval(() => this.sendHeartbeat(), 30000);  // Keep session alive
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
        
        // Update all symbol displays (check if element exists first)
        const headerSymbol = document.getElementById('header-symbol');
        const chartSymbol = document.getElementById('chart-symbol');
        if (headerSymbol) headerSymbol.textContent = symbol;
        if (chartSymbol) chartSymbol.textContent = symbol;
    } catch (err) {
        console.log('Symbol update error:', err);
    }
}

// Call on init and every 15 seconds
document.addEventListener('DOMContentLoaded', updateCurrentSymbol);
setInterval(updateCurrentSymbol, 15000);
