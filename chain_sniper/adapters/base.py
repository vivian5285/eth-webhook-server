#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""链无关适配器接口。signals/、execution/risk_gate.py、execution/exit_manager.py
只依赖这个接口，从不判断"是BSC还是Solana"——两条链从第一天起共用一套框架。"""
from abc import ABC, abstractmethod


class ChainAdapter(ABC):
    chain_name = ""  # "bsc" | "solana"，子类覆盖

    @abstractmethod
    def get_watched_wallet_events(self, since_ts):
        """返回自 since_ts 起、监控名单里地址产生的新链上事件 list[WalletEvent]。"""
        raise NotImplementedError

    @abstractmethod
    def get_token_metadata(self, token_address):
        """返回 TokenInfo。"""
        raise NotImplementedError

    @abstractmethod
    def get_recent_buyers(self, token_address, window_sec):
        """返回 BuyerStats，用于真实增长过滤（独立买家数，反刷量）。"""
        raise NotImplementedError

    @abstractmethod
    def get_current_price(self, token_address):
        """返回 Decimal 价格，失败返回 None（调用方需容忍 RPC 抖动，不崩循环）。"""
        raise NotImplementedError

    @abstractmethod
    def execute_buy(self, token_address, quote_amount, slippage_bps):
        """返回 TradeResult。DRY_RUN 模式下不应调用本方法——由上层 buyer.py 拦截。"""
        raise NotImplementedError

    @abstractmethod
    def execute_sell(self, token_address, token_amount, slippage_bps):
        """返回 TradeResult。DRY_RUN 模式下不应调用本方法——由上层 exit_manager.py 拦截。"""
        raise NotImplementedError

    @abstractmethod
    def get_wallet_balance(self):
        """返回热钱包原生 gas 代币余额（Decimal），用于余额不足告警。"""
        raise NotImplementedError
