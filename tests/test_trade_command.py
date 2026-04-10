"""
Tests for TradeCommand DTO and the unified command system.

Verifies:
- TradeCommand creation, serialization, deserialization
- Factory methods (hold, close, entry)
- Backward compatibility with prediction dict format
- TradeResult creation
- BacktestSimulator as BaseCommandExecutor
"""

import json
import pytest

from src.core.commands.models import TradeAction, TradeCommand, TradeResult


class TestTradeAction:
    def test_is_entry(self):
        assert TradeAction.BUY.is_entry is True
        assert TradeAction.SELL.is_entry is True
        assert TradeAction.HOLD.is_entry is False
        assert TradeAction.CLOSE.is_entry is False

    def test_is_exit(self):
        assert TradeAction.CLOSE.is_exit is True
        assert TradeAction.CLOSE_PARTIAL.is_exit is True
        assert TradeAction.BUY.is_exit is False

    def test_is_hold(self):
        assert TradeAction.HOLD.is_hold is True
        assert TradeAction.BUY.is_hold is False

    def test_values(self):
        assert TradeAction.BUY.value == "buy"
        assert TradeAction.SELL.value == "sell"
        assert TradeAction.HOLD.value == "hold"
        assert TradeAction.CLOSE.value == "close"
        assert TradeAction.CLOSE_PARTIAL.value == "close_partial"


class TestTradeCommandCreation:
    def test_basic_creation(self):
        cmd = TradeCommand(
            symbol="BTC-USDT",
            action=TradeAction.BUY,
            confidence=0.85,
            current_price=50000.0,
            reason="Strong signal",
        )
        assert cmd.symbol == "BTC-USDT"
        assert cmd.action == TradeAction.BUY
        assert cmd.confidence == 0.85
        assert cmd.current_price == 50000.0

    def test_defaults(self):
        cmd = TradeCommand(
            symbol="ETH-USDT",
            action=TradeAction.HOLD,
            confidence=0.0,
            current_price=3000.0,
        )
        assert cmd.stop_loss is None
        assert cmd.take_profit is None
        assert cmd.size_pct is None
        assert cmd.percentage == 1.0
        assert cmd.score == 0
        assert cmd.regime == "UNKNOWN"
        assert cmd.strategy == ""
        assert cmd.metadata == {}
        assert cmd.timestamp is not None


class TestTradeCommandFactoryMethods:
    def test_hold(self):
        cmd = TradeCommand.hold(
            symbol="BTC-USDT",
            current_price=50000.0,
            reason="No MACD cross",
            strategy="MACDX",
            score=3,
            max_score=9,
            confirmations=2,
            regime="RANGING",
        )
        assert cmd.action == TradeAction.HOLD
        assert cmd.confidence == 0.0
        assert cmd.score == 3
        assert cmd.max_score == 9
        assert cmd.strategy == "MACDX"
        assert cmd.regime == "RANGING"

    def test_close(self):
        cmd = TradeCommand.close(
            symbol="BTC-USDT",
            current_price=51000.0,
            reason="RSI overbought",
            confidence=0.9,
            strategy="MACDX",
        )
        assert cmd.action == TradeAction.CLOSE
        assert cmd.percentage == 1.0
        assert cmd.confidence == 0.9

    def test_close_partial(self):
        cmd = TradeCommand.close(
            symbol="BTC-USDT",
            current_price=51000.0,
            reason="Take partial profit",
            percentage=0.5,
            strategy="HYBRID",
        )
        assert cmd.action == TradeAction.CLOSE_PARTIAL
        assert cmd.percentage == 0.5

    def test_entry_buy(self):
        cmd = TradeCommand.entry(
            symbol="BTC-USDT",
            side="BUY",
            current_price=50000.0,
            confidence=0.8,
            reason="MACD crossover",
            stop_loss=49000.0,
            take_profit=52000.0,
            size_pct=10.5,
            strategy="MACDX",
            score=7,
            max_score=9,
            confirmations=5,
            regime="TRENDING",
        )
        assert cmd.action == TradeAction.BUY
        assert cmd.stop_loss == 49000.0
        assert cmd.take_profit == 52000.0
        assert cmd.size_pct == 10.5
        assert cmd.score == 7

    def test_entry_sell(self):
        cmd = TradeCommand.entry(
            symbol="BTC-USDT",
            side="SELL",
            current_price=50000.0,
            confidence=0.75,
            strategy="MACDX",
        )
        assert cmd.action == TradeAction.SELL


class TestTradeCommandSerialization:
    def _make_command(self):
        return TradeCommand.entry(
            symbol="BTC-USDT",
            side="BUY",
            current_price=50000.0,
            confidence=0.85,
            reason="Test signal",
            stop_loss=49000.0,
            take_profit=52000.0,
            size_pct=10.0,
            strategy="MACDX",
            score=6,
            max_score=9,
            confirmations=4,
            regime="TRENDING",
        )

    def test_to_dict(self):
        cmd = self._make_command()
        d = cmd.to_dict()
        assert d["symbol"] == "BTC-USDT"
        assert d["action"] == "buy"
        assert d["confidence"] == 0.85
        assert d["current_price"] == 50000.0
        assert d["stop_loss"] == 49000.0
        assert d["take_profit"] == 52000.0
        assert d["size_pct"] == 10.0
        assert d["score"] == 6
        assert d["strategy"] == "MACDX"

    def test_to_dict_omits_none_optionals(self):
        cmd = TradeCommand.hold(symbol="BTC-USDT", current_price=50000.0)
        d = cmd.to_dict()
        assert "stop_loss" not in d
        assert "take_profit" not in d
        assert "size_pct" not in d
        assert "percentage" not in d  # percentage==1.0 is omitted

    def test_to_json(self):
        cmd = self._make_command()
        j = cmd.to_json()
        parsed = json.loads(j)
        assert parsed["action"] == "buy"
        assert parsed["symbol"] == "BTC-USDT"

    def test_from_dict(self):
        cmd = self._make_command()
        d = cmd.to_dict()
        restored = TradeCommand.from_dict(d)
        assert restored.symbol == cmd.symbol
        assert restored.action == cmd.action
        assert restored.confidence == cmd.confidence
        assert restored.stop_loss == cmd.stop_loss
        assert restored.take_profit == cmd.take_profit
        assert restored.strategy == cmd.strategy

    def test_from_dict_unknown_action_defaults_to_hold(self):
        d = {"symbol": "BTC-USDT", "action": "unknown_action", "current_price": 50000.0}
        cmd = TradeCommand.from_dict(d)
        assert cmd.action == TradeAction.HOLD

    def test_roundtrip(self):
        """Test dict → TradeCommand → dict roundtrip."""
        original = {
            "symbol": "ETH-USDT",
            "action": "sell",
            "confidence": 0.7,
            "current_price": 3000.0,
            "reason": "Bearish divergence",
            "stop_loss": 3100.0,
            "take_profit": 2800.0,
            "score": 5,
            "max_score": 8,
        }
        cmd = TradeCommand.from_dict(original)
        result = cmd.to_dict()
        assert result["symbol"] == original["symbol"]
        assert result["action"] == original["action"]
        assert result["confidence"] == original["confidence"]
        assert result["stop_loss"] == original["stop_loss"]


class TestTradeCommandBackwardCompatibility:
    """Test that TradeCommand.to_dict() produces output compatible with executor.execute_prediction()."""

    def test_buy_prediction_format(self):
        cmd = TradeCommand.entry(
            symbol="BTC-USDT",
            side="BUY",
            current_price=50000.0,
            confidence=0.85,
            reason="MACDX LONG",
            stop_loss=49000.0,
            take_profit=52000.0,
            size_pct=10.0,
        )
        d = cmd.to_dict()
        # executor.execute_prediction() expects these keys
        assert "symbol" in d
        assert "action" in d
        assert "confidence" in d
        assert "current_price" in d
        assert "reason" in d
        assert "stop_loss" in d
        assert "take_profit" in d
        assert d["action"] == "buy"

    def test_close_prediction_format(self):
        cmd = TradeCommand.close(
            symbol="BTC-USDT",
            current_price=51000.0,
            reason="Exit signal",
        )
        d = cmd.to_dict()
        assert d["action"] == "close"
        assert d["symbol"] == "BTC-USDT"

    def test_hold_prediction_format(self):
        cmd = TradeCommand.hold(
            symbol="BTC-USDT",
            current_price=50000.0,
            reason="No signal",
        )
        d = cmd.to_dict()
        assert d["action"] == "hold"


class TestTradeResult:
    def test_success_result(self):
        cmd = TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0, confidence=0.8
        )
        result = TradeResult(
            success=True,
            command=cmd,
            order_id="12345",
            executed_price=50001.0,
            executed_quantity=0.1,
            message="Order filled",
        )
        assert result.success is True
        assert result.order_id == "12345"
        assert result.command.action == TradeAction.BUY

    def test_failure_result(self):
        cmd = TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0, confidence=0.8
        )
        result = TradeResult(
            success=False,
            command=cmd,
            message="Insufficient balance",
        )
        assert result.success is False
        assert result.order_id is None

    def test_to_dict(self):
        cmd = TradeCommand.hold(symbol="BTC-USDT", current_price=50000.0)
        result = TradeResult(success=True, command=cmd, message="HOLD")
        d = result.to_dict()
        assert d["success"] is True
        assert d["command"]["action"] == "hold"


class TestBacktestEngineConfig:
    """Test BacktestEngine reads config values from correct paths."""

    def test_timeframe_from_preset(self):
        """Engine reads timeframe from config.preset.timeframe, not top-level."""
        from unittest.mock import patch, MagicMock

        mock_config = {
            "preset": {
                "timeframe": "1h",
                "leverage": 6,
            },
            "position": {
                "size_percent": 32,
            },
            "signal_rules": {},
        }

        with patch("src.backtest.engine.resolve_symbol_config", return_value=mock_config):
            with patch("src.backtest.engine.DataLoader") as MockLoader:
                with patch("src.backtest.engine.SignalGenerator"):
                    from src.backtest.engine import BacktestEngine
                    engine = BacktestEngine("BTCUSDT", "MACDX", 1000.0)

                    assert engine.timeframe == "1h"
                    MockLoader.assert_called_once_with("BTCUSDT", "1h")

    def test_leverage_from_preset(self):
        """Engine reads leverage from config.preset.leverage."""
        from unittest.mock import patch

        mock_config = {
            "preset": {
                "timeframe": "1h",
                "leverage": 10,
            },
            "position": {
                "size_percent": 20,
            },
            "signal_rules": {},
        }

        with patch("src.backtest.engine.resolve_symbol_config", return_value=mock_config):
            with patch("src.backtest.engine.DataLoader"):
                with patch("src.backtest.engine.SignalGenerator"):
                    from src.backtest.engine import BacktestEngine
                    engine = BacktestEngine("BTCUSDT", "MACDX", 1000.0)

                    assert engine.simulator.leverage == 10

    def test_position_size_from_config(self):
        """Engine reads position size from config.position.size_percent and converts to fraction."""
        from unittest.mock import patch

        mock_config = {
            "preset": {
                "timeframe": "15m",
                "leverage": 5,
            },
            "position": {
                "size_percent": 32,
            },
            "signal_rules": {},
        }

        with patch("src.backtest.engine.resolve_symbol_config", return_value=mock_config):
            with patch("src.backtest.engine.DataLoader"):
                with patch("src.backtest.engine.SignalGenerator"):
                    from src.backtest.engine import BacktestEngine
                    engine = BacktestEngine("BTCUSDT", "MACDX", 1000.0)

                    assert engine.simulator.position_size_percent == pytest.approx(0.32)

    def test_fallback_defaults(self):
        """Engine uses sane defaults when preset/position are missing."""
        from unittest.mock import patch

        mock_config = {}

        with patch("src.backtest.engine.resolve_symbol_config", return_value=mock_config):
            with patch("src.backtest.engine.DataLoader"):
                with patch("src.backtest.engine.SignalGenerator"):
                    from src.backtest.engine import BacktestEngine
                    engine = BacktestEngine("BTCUSDT", "MACDX", 1000.0)

                    assert engine.timeframe == "15m"
                    assert engine.simulator.leverage == 5.0
                    assert engine.simulator.position_size_percent == pytest.approx(0.10)

    def test_config_passed_to_signal_generator(self):
        """Engine passes resolved config to SignalGenerator."""
        from unittest.mock import patch, call

        mock_config = {
            "preset": {"timeframe": "1h", "leverage": 6},
            "position": {"size_percent": 10},
            "signal_rules": {"min_score": 5},
        }

        with patch("src.backtest.engine.resolve_symbol_config", return_value=mock_config):
            with patch("src.backtest.engine.DataLoader"):
                with patch("src.backtest.engine.SignalGenerator") as MockSG:
                    from src.backtest.engine import BacktestEngine
                    engine = BacktestEngine("BTCUSDT", "MACDX", 1000.0)

                    MockSG.assert_called_once_with("MACDX", mock_config)


class TestBacktestSimulatorAsExecutor:
    """Test BacktestSimulator as a BaseCommandExecutor."""

    def test_implements_base_executor(self):
        """BacktestSimulator is a proper BaseCommandExecutor."""
        from src.core.commands.executor import BaseCommandExecutor
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)
        assert isinstance(bt, BaseCommandExecutor)

    def test_execute_entry_and_close(self):
        """Full lifecycle: entry → hold → close."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0, leverage=5.0, position_size_percent=0.1)

        # 1. Entry
        cmd1 = TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.85, stop_loss=49000.0, take_profit=52000.0,
            size_pct=10.0, strategy="MACDX",
        )
        r1 = bt.execute(cmd1)
        assert r1.success
        assert "BTC-USDT" in bt.positions

        # 2. Hold
        cmd2 = TradeCommand.hold(symbol="BTC-USDT", current_price=50500.0, strategy="MACDX")
        r2 = bt.execute(cmd2)
        assert r2.success
        assert "BTC-USDT" in bt.positions  # Still open

        # 3. Close
        cmd3 = TradeCommand.close(symbol="BTC-USDT", current_price=51000.0, reason="TP hit", strategy="MACDX")
        r3 = bt.execute(cmd3)
        assert r3.success
        assert "BTC-USDT" not in bt.positions

    def test_execute_duplicate_entry_fails(self):
        """Cannot open a second position for the same symbol."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        cmd1 = TradeCommand.entry(symbol="BTC-USDT", side="BUY", current_price=50000.0, confidence=0.8)
        r1 = bt.execute(cmd1)
        assert r1.success

        cmd2 = TradeCommand.entry(symbol="BTC-USDT", side="SELL", current_price=50100.0, confidence=0.7)
        r2 = bt.execute(cmd2)
        assert not r2.success

    def test_execute_close_without_position_fails(self):
        """Cannot close a position that doesn't exist."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        cmd = TradeCommand.close(symbol="BTC-USDT", current_price=50000.0, reason="test")
        r = bt.execute(cmd)
        assert not r.success

    def test_pnl_calculation_long(self):
        """Verify PnL is calculated correctly for a profitable LONG."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0, leverage=5.0, position_size_percent=0.1)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.8, size_pct=10.0,
        ))
        bt.execute(TradeCommand.close(
            symbol="BTC-USDT", current_price=51000.0, reason="profit",
        ))

        metrics = bt.get_metrics()
        assert metrics["total_trades"] == 1
        assert metrics["total_pnl"] > 0  # Should be profitable

    def test_pnl_calculation_short(self):
        """Verify PnL is calculated correctly for a profitable SHORT."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0, leverage=5.0, position_size_percent=0.1)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="SELL", current_price=50000.0,
            confidence=0.8, size_pct=10.0,
        ))
        bt.execute(TradeCommand.close(
            symbol="BTC-USDT", current_price=49000.0, reason="profit",
        ))

        metrics = bt.get_metrics()
        assert metrics["total_trades"] == 1
        assert metrics["total_pnl"] > 0  # Should be profitable

    def test_check_sl_tp_command(self):
        """SL/TP check returns TradeCommand.close() when triggered."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.8, stop_loss=49000.0, take_profit=52000.0,
        ))

        # Price above entry but below TP — no trigger
        cmd = bt.check_sl_tp_command("BTC-USDT", 50500.0)
        assert cmd is None

        # Price hits SL
        cmd = bt.check_sl_tp_command("BTC-USDT", 48500.0)
        assert cmd is not None
        assert cmd.action == TradeAction.CLOSE
        assert "SL" in cmd.reason

    def test_check_sl_tp_command_tp_hit(self):
        """TP hit returns close command."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.8, stop_loss=49000.0, take_profit=52000.0,
        ))

        cmd = bt.check_sl_tp_command("BTC-USDT", 53000.0)
        assert cmd is not None
        assert cmd.action == TradeAction.CLOSE
        assert "TP" in cmd.reason

    def test_command_history_tracked(self):
        """All commands are recorded in history."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        bt.execute(TradeCommand.entry(symbol="BTC-USDT", side="BUY", current_price=50000.0, confidence=0.8))
        bt.execute(TradeCommand.hold(symbol="BTC-USDT", current_price=50500.0))
        bt.execute(TradeCommand.close(symbol="BTC-USDT", current_price=51000.0))

        assert len(bt.command_history) == 3
        assert bt.command_history[0].action == TradeAction.BUY
        assert bt.command_history[1].action == TradeAction.HOLD
        assert bt.command_history[2].action == TradeAction.CLOSE

    def test_commands_serializable_for_replay(self):
        """Verify that command history can be serialized and replayed."""
        commands = [
            TradeCommand.entry(symbol="BTC-USDT", side="BUY", current_price=50000.0, confidence=0.8, strategy="MACDX"),
            TradeCommand.hold(symbol="BTC-USDT", current_price=50500.0, strategy="MACDX"),
            TradeCommand.close(symbol="BTC-USDT", current_price=51000.0, reason="Exit", strategy="MACDX"),
        ]

        # Serialize all commands to JSON
        serialized = [cmd.to_json() for cmd in commands]

        # Deserialize and verify
        restored = [TradeCommand.from_dict(json.loads(s)) for s in serialized]
        assert len(restored) == 3
        assert restored[0].action == TradeAction.BUY
        assert restored[0].current_price == 50000.0
        assert restored[1].action == TradeAction.HOLD
        assert restored[2].action == TradeAction.CLOSE
        assert restored[2].reason == "Exit"

    def test_sell_entry_creates_short_position(self):
        """SELL command creates a SHORT position internally."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="SELL", current_price=50000.0,
            confidence=0.8, stop_loss=51000.0, take_profit=48000.0,
        ))

        assert "BTC-USDT" in bt.positions
        assert bt.positions["BTC-USDT"]["side"] == "SHORT"

    def test_sl_tp_from_command_used(self):
        """SL/TP from TradeCommand are passed to the position."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.8, stop_loss=49500.0, take_profit=51500.0,
        ))

        pos = bt.positions["BTC-USDT"]
        assert pos["sl_price"] == 49500.0
        assert pos["tp_price"] == 51500.0

    def test_update_unrealized_pnl(self):
        """update_unrealized_pnl updates P&L without closing positions."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.8, stop_loss=49000.0, take_profit=52000.0,
        ))

        # Price goes up — unrealized PnL should be positive
        bt.update_unrealized_pnl({"BTC-USDT": 51000.0})
        assert bt.positions["BTC-USDT"]["unrealized_pnl"] > 0
        assert "BTC-USDT" in bt.positions  # Position NOT closed

        # Price goes down — unrealized PnL should go below SL but position stays open
        bt.update_unrealized_pnl({"BTC-USDT": 48000.0})
        assert bt.positions["BTC-USDT"]["unrealized_pnl"] < 0
        assert "BTC-USDT" in bt.positions  # Still NOT closed (no SL/TP check)

    def test_update_positions_closes_on_sl(self):
        """update_positions (legacy) checks SL/TP and closes via TradeCommand."""
        from src.backtest.simulator import BacktestSimulator
        bt = BacktestSimulator(initial_balance=10000.0)

        bt.execute(TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.8, stop_loss=49000.0, take_profit=52000.0,
        ))

        # Price hits SL — update_positions should close via command
        bt.update_positions({"BTC-USDT": 48500.0})
        assert "BTC-USDT" not in bt.positions  # Closed by SL
        assert bt.pnl_tracker.trades  # Trade recorded
