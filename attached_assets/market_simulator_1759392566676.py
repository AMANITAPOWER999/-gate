import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import logging

class MarketSimulator:
    def __init__(self, initial_price=3000, volatility=0.02):
        """
        Симулятор рыночных данных для ETH/USDT
        
        Args:
            initial_price: Начальная цена ETH
            volatility: Волатильность (0.02 = 2%)
        """
        self.current_price = initial_price
        self.volatility = volatility
        self.price_history = []
        self.last_update = datetime.utcnow()
        
        # Хранение данных для разных таймфреймов
        self.ohlcv_data = {
            "1m": [],
            "3m": [],
            "15m": []
        }
        
        # Инициализация начальных данных
        self._initialize_historical_data()
        
    def _initialize_historical_data(self):
        """Создаём исторические данные для расчёта PSAR"""
        base_time = datetime.utcnow() - timedelta(hours=4)
        
        for tf in ["1m", "3m", "15m"]:
            interval_minutes = int(tf.replace('m', ''))
            num_candles = 240 // interval_minutes  # 4 часа данных
            
            current_price = self.current_price
            
            for i in range(num_candles):
                candle_time = base_time + timedelta(minutes=i * interval_minutes)
                
                # Генерируем OHLCV свечу
                open_price = current_price
                
                # Случайное изменение цены
                price_change = np.random.normal(0, self.volatility * current_price)
                close_price = max(open_price + price_change, 1)  # Цена не может быть меньше 1
                
                # High и Low относительно Open и Close
                high_offset = np.random.uniform(0, self.volatility * current_price)
                low_offset = np.random.uniform(0, self.volatility * current_price)
                
                high_price = max(open_price, close_price) + high_offset
                low_price = min(open_price, close_price) - low_offset
                low_price = max(low_price, 1)  # Не меньше 1
                
                volume = np.random.uniform(100, 1000)
                
                candle = {
                    'timestamp': int(candle_time.timestamp() * 1000),
                    'open': round(open_price, 2),
                    'high': round(high_price, 2),
                    'low': round(low_price, 2),
                    'close': round(close_price, 2),
                    'volume': round(volume, 2)
                }
                
                self.ohlcv_data[tf].append(candle)
                current_price = close_price
        
        self.current_price = current_price
        logging.info(f"Initialized simulator with price: ${self.current_price:.2f}")
    
    def get_current_price(self):
        """Возвращает текущую цену"""
        self._update_price()
        return self.current_price
    
    def _update_price(self):
        """Обновляет текущую цену с учётом времени"""
        now = datetime.utcnow()
        time_diff = (now - self.last_update).total_seconds()
        
        if time_diff > 10:  # Обновляем каждые 10 секунд
            # Генерируем новое изменение цены
            price_change = np.random.normal(0, self.volatility * self.current_price * 0.1)
            self.current_price = max(self.current_price + price_change, 1)
            self.current_price = round(self.current_price, 2)
            
            self.last_update = now
            
            # Обновляем последние свечи
            self._update_candles()
    
    def _update_candles(self):
        """Обновляет последние свечи для всех таймфреймов"""
        now = datetime.utcnow()
        
        for tf in ["1m", "3m", "15m"]:
            interval_minutes = int(tf.replace('m', ''))
            
            if len(self.ohlcv_data[tf]) > 0:
                last_candle = self.ohlcv_data[tf][-1]
                last_time = datetime.fromtimestamp(last_candle['timestamp'] / 1000)
                
                # Проверяем, нужно ли создать новую свечу
                time_diff = (now - last_time).total_seconds()
                
                if time_diff >= interval_minutes * 60:  # Время для новой свечи
                    # Создаём новую свечу
                    new_candle = self._create_new_candle(last_candle['close'], now)
                    self.ohlcv_data[tf].append(new_candle)
                    
                    # Ограничиваем количество свечей
                    if len(self.ohlcv_data[tf]) > 300:
                        self.ohlcv_data[tf] = self.ohlcv_data[tf][-200:]
                else:
                    # Обновляем текущую свечу
                    self.ohlcv_data[tf][-1]['close'] = self.current_price
                    self.ohlcv_data[tf][-1]['high'] = max(self.ohlcv_data[tf][-1]['high'], self.current_price)
                    self.ohlcv_data[tf][-1]['low'] = min(self.ohlcv_data[tf][-1]['low'], self.current_price)
    
    def _create_new_candle(self, previous_close, timestamp):
        """Создаёт новую свечу"""
        open_price = previous_close
        
        # Генерируем изменение для этой свечи
        price_change = np.random.normal(0, self.volatility * open_price * 0.5)
        close_price = max(open_price + price_change, 1)
        
        # High и Low
        high_offset = np.random.uniform(0, self.volatility * open_price * 0.3)
        low_offset = np.random.uniform(0, self.volatility * open_price * 0.3)
        
        high_price = max(open_price, close_price) + high_offset
        low_price = min(open_price, close_price) - low_offset
        low_price = max(low_price, 1)
        
        volume = np.random.uniform(100, 1000)
        
        return {
            'timestamp': int(timestamp.timestamp() * 1000),
            'open': round(open_price, 2),
            'high': round(high_price, 2),
            'low': round(low_price, 2),
            'close': round(close_price, 2),
            'volume': round(volume, 2)
        }
    
    def fetch_ohlcv(self, timeframe, limit=200):
        """
        Возвращает OHLCV данные для указанного таймфрейма
        
        Args:
            timeframe: '1m', '3m', '15m'
            limit: количество свечей
        
        Returns:
            List of [timestamp, open, high, low, close, volume]
        """
        self._update_price()
        
        if timeframe not in self.ohlcv_data:
            logging.error(f"Timeframe {timeframe} not supported")
            return []
        
        candles = self.ohlcv_data[timeframe][-limit:]
        
        # Конвертируем в формат ccxt
        ohlcv = []
        for candle in candles:
            ohlcv.append([
                candle['timestamp'],
                candle['open'],
                candle['high'],
                candle['low'],
                candle['close'],
                candle['volume']
            ])
        
        return ohlcv
    
    def get_ticker(self):
        """Возвращает ticker данные в формате ccxt"""
        self._update_price()
        
        return {
            'symbol': 'ETH/USDT',
            'last': self.current_price,
            'bid': self.current_price - 0.1,
            'ask': self.current_price + 0.1,
            'high': self.current_price * 1.02,
            'low': self.current_price * 0.98,
            'volume': np.random.uniform(1000, 10000)
        }