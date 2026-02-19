/**
 * Control Panel Component
 * Price and volatility input controls
 * Saves parameter presets per symbol in localStorage
 */
import { useState, useEffect, useMemo, useCallback } from 'react';
import { Card, Form, Select, InputNumber, DatePicker, Button, Row, Col, message } from 'antd';
import { CalculatorOutlined, SaveOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { marketPriceService } from '../services/marketPriceService';
import { pnlService } from '../services/pnlService';
import { useAppContext, ActionTypes } from '../context/AppContext';

const { Option } = Select;
const PRESETS_KEY = 'control_panel_presets';

function loadPresets() {
  try {
    return JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}');
  } catch { return {}; }
}

function savePresets(presets) {
  localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
}

export default function ControlPanel() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const { state, dispatch } = useAppContext();

  // Get unique symbols from positions
  const symbols = useMemo(() => {
    const uniqueSymbols = [...new Set(state.positions.map((p) => p.underlying_symbol))];
    return uniqueSymbols.sort();
  }, [state.positions]);

  // Load preset for a symbol and fill form
  const applyPreset = useCallback((symbol) => {
    const presets = loadPresets();
    const preset = presets[symbol];
    if (preset) {
      form.setFieldsValue({
        current_price: preset.current_price,
        implied_volatility: preset.implied_volatility,
        contract_multiplier: preset.contract_multiplier,
        target_date: preset.target_date ? dayjs(preset.target_date) : dayjs(),
      });
    } else {
      // No preset — reset to defaults but keep symbol
      form.setFieldsValue({
        current_price: undefined,
        implied_volatility: 25,
        contract_multiplier: 1,
        target_date: dayjs(),
      });
    }
  }, [form]);

  // Save current form values as preset for the selected symbol
  const saveCurrentPreset = useCallback(() => {
    const values = form.getFieldsValue();
    const symbol = values.underlying_symbol;
    if (!symbol) return;
    const presets = loadPresets();
    presets[symbol] = {
      current_price: values.current_price,
      implied_volatility: values.implied_volatility,
      contract_multiplier: values.contract_multiplier,
      target_date: values.target_date ? values.target_date.format('YYYY-MM-DD') : null,
    };
    savePresets(presets);
  }, [form]);

  // When symbol changes, apply its preset
  const handleSymbolChange = useCallback((symbol) => {
    applyPreset(symbol);
  }, [applyPreset]);

  // Set default symbol when positions change
  useEffect(() => {
    if (symbols.length > 0 && !form.getFieldValue('underlying_symbol')) {
      const firstSymbol = symbols[0];
      form.setFieldValue('underlying_symbol', firstSymbol);
      applyPreset(firstSymbol);
    }
  }, [symbols, form, applyPreset]);

  const handleCalculate = async (values) => {
    if (symbols.length === 0) {
      message.warning('请先添加持仓');
      return;
    }

    setLoading(true);
    try {
      await marketPriceService.update(values.underlying_symbol, values.current_price);

      dispatch({
        type: ActionTypes.SET_MARKET_PRICE,
        payload: { symbol: values.underlying_symbol, price: values.current_price },
      });

      dispatch({
        type: ActionTypes.SET_VOLATILITY,
        payload: { symbol: values.underlying_symbol, volatility: values.implied_volatility },
      });

      const requestData = {
        underlying_symbol: values.underlying_symbol,
        current_price: values.current_price,
        implied_volatility: values.implied_volatility / 100,
        price_range_percent: 1.5,
        portfolio_id: state.activePortfolioId,
        contract_multiplier: values.contract_multiplier || 1.0,
      };

      if (values.target_date) {
        requestData.target_date = values.target_date.format('YYYY-MM-DD');
      }

      const pnlData = await pnlService.calculate(requestData);

      dispatch({ type: ActionTypes.SET_PNL_DATA, payload: pnlData });

      // Auto-save preset on successful calculation
      saveCurrentPreset();
      message.success('计算完成');
    } catch (error) {
      message.error('计算失败');
      console.error('Calculation error:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card title="控制面板" style={{ marginBottom: 16 }}>
      <Form
        form={form}
        layout="vertical"
        onFinish={handleCalculate}
        initialValues={{
          implied_volatility: 25,
          target_date: dayjs(),
          contract_multiplier: 1,
        }}
      >
        <Row gutter={16}>
          <Col xs={24} sm={12} md={5}>
            <Form.Item
              name="underlying_symbol"
              label="标的代码"
              rules={[{ required: true, message: '请选择标的' }]}
            >
              <Select placeholder="选择标的" disabled={symbols.length === 0} onChange={handleSymbolChange}>
                {symbols.map((symbol) => (
                  <Option key={symbol} value={symbol}>{symbol}</Option>
                ))}
              </Select>
            </Form.Item>
          </Col>

          <Col xs={24} sm={12} md={4}>
            <Form.Item
              name="current_price"
              label="当前价格"
              rules={[
                { required: true, message: '请输入当前价格' },
                { type: 'number', min: 0.01, message: '价格必须大于0' },
              ]}
            >
              <InputNumber style={{ width: '100%' }} placeholder="例如: 150.00" precision={2} min={0.01} prefix="$" />
            </Form.Item>
          </Col>

          <Col xs={24} sm={12} md={5}>
            <Form.Item
              name="target_date"
              label="当前日期"
              rules={[{ required: true, message: '请选择日期' }]}
            >
              <DatePicker style={{ width: '100%' }} format="YYYY-MM-DD" />
            </Form.Item>
          </Col>

          <Col xs={24} sm={12} md={4}>
            <Form.Item
              name="implied_volatility"
              label="隐含波动率 (%)"
              rules={[
                { required: true, message: '请输入波动率' },
                { type: 'number', min: 0.1, max: 500, message: '波动率范围: 0.1-500%' },
              ]}
            >
              <InputNumber style={{ width: '100%' }} placeholder="例如: 25" precision={2} min={0.1} max={500} suffix="%" />
            </Form.Item>
          </Col>

          <Col xs={24} sm={12} md={2}>
            <Form.Item
              name="contract_multiplier"
              label="合约系数"
              rules={[
                { required: true, message: '请输入合约系数' },
                { type: 'number', min: 0.001, message: '系数必须大于0' },
              ]}
            >
              <InputNumber style={{ width: '100%' }} placeholder="1" precision={3} min={0.001} suffix="x" />
            </Form.Item>
          </Col>

          <Col xs={24} sm={12} md={2}>
            <Form.Item label=" ">
              <Button
                icon={<SaveOutlined />}
                block
                onClick={saveCurrentPreset}
                title="保存当前参数为该标的默认值"
              >
                保存
              </Button>
            </Form.Item>
          </Col>

          <Col xs={24} sm={12} md={2}>
            <Form.Item label=" ">
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                icon={<CalculatorOutlined />}
                block
                disabled={symbols.length === 0}
              >
                计算
              </Button>
            </Form.Item>
          </Col>
        </Row>
      </Form>
    </Card>
  );
}
