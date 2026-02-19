/**
 * Position Form Component
 * Form for creating and editing option positions
 */
import { useState, useEffect, useMemo } from 'react';
import { Form, InputNumber, Select, DatePicker, Button, AutoComplete, message } from 'antd';
import dayjs from 'dayjs';
import { positionService } from '../services/positionService';
import { useAppContext, ActionTypes } from '../context/AppContext';

const { Option } = Select;

export default function PositionForm({ editingPosition, onSuccess, onCancel }) {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const { state, dispatch } = useAppContext();

  // Collect unique symbols from existing positions for autocomplete
  const symbolOptions = useMemo(() => {
    const unique = [...new Set(state.positions.map((p) => p.underlying_symbol))].sort();
    return unique.map((s) => ({ value: s }));
  }, [state.positions]);

  useEffect(() => {
    if (editingPosition) {
      form.setFieldsValue({
        ...editingPosition,
        expiration_date: dayjs(editingPosition.expiration_date),
      });
    } else {
      form.resetFields();
    }
  }, [editingPosition, form]);

  const handleSubmit = async (values) => {
    setLoading(true);
    try {
      const formattedValues = {
        ...values,
        portfolio_id: state.activePortfolioId,
        expiration_date: values.expiration_date.format('YYYY-MM-DD'),
      };

      console.log('Submitting values:', JSON.stringify(formattedValues, null, 2));

      if (editingPosition) {
        const updated = await positionService.update(editingPosition.id, formattedValues);
        dispatch({ type: ActionTypes.UPDATE_POSITION, payload: updated });
        message.success('持仓更新成功');
      } else {
        const created = await positionService.create(formattedValues);
        dispatch({ type: ActionTypes.ADD_POSITION, payload: created });
        message.success('持仓添加成功');
      }

      form.resetFields();
      if (onSuccess) onSuccess();
    } catch (error) {
      message.error(editingPosition ? '更新失败' : '添加失败');
      console.error('Form submission error:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={handleSubmit}
      initialValues={{
        option_type: 'PUT',
        quantity: -1,
      }}
    >
      <Form.Item
        name="underlying_symbol"
        label="标的代码"
        rules={[{ required: true, message: '请输入标的代码' }]}
      >
        <AutoComplete
          options={symbolOptions}
          placeholder="例如: BTC"
          maxLength={20}
          filterOption={(input, option) =>
            option.value.toUpperCase().includes(input.toUpperCase())
          }
        />
      </Form.Item>

      <Form.Item name="option_type" label="期权类型" rules={[{ required: true }]}>
        <Select>
          <Option value="PUT">Put</Option>
          <Option value="CALL">Call</Option>
        </Select>
      </Form.Item>

      <Form.Item
        name="strike_price"
        label="行权价"
        rules={[
          { required: true, message: '请输入行权价' },
          { type: 'number', min: 0.01, message: '行权价必须大于0' },
        ]}
      >
        <InputNumber style={{ width: '100%' }} placeholder="例如: 150.00" precision={2} min={0.01} />
      </Form.Item>

      <Form.Item
        name="expiration_date"
        label="到期日"
        rules={[{ required: true, message: '请选择到期日' }]}
      >
        <DatePicker
          style={{ width: '100%' }}
          format="YYYY-MM-DD"
          disabledDate={(current) => current && current < dayjs().startOf('day')}
        />
      </Form.Item>

      <Form.Item
        name="quantity"
        label="合约数量"
        rules={[
          { required: true, message: '请输入合约数量' },
          { type: 'number', message: '必须是数字' },
          {
            validator: (_, value) => {
              if (value === 0) return Promise.reject('数量不能为0');
              return Promise.resolve();
            },
          },
        ]}
        tooltip="正数表示买入，负数表示卖出"
      >
        <InputNumber style={{ width: '100%' }} placeholder="例如: -1 (卖出1张)" />
      </Form.Item>

      <Form.Item
        name="entry_price"
        label="开仓价格（权利金）"
        rules={[
          { required: true, message: '请输入开仓价格' },
          { type: 'number', min: 0, message: '价格不能为负' },
        ]}
      >
        <InputNumber style={{ width: '100%' }} placeholder="例如: 2.50" precision={4} min={0} />
      </Form.Item>

      <Form.Item>
        <Button type="primary" htmlType="submit" loading={loading} block>
          {editingPosition ? '更新持仓' : '添加持仓'}
        </Button>
        {onCancel && (
          <Button onClick={onCancel} style={{ marginTop: 8 }} block>
            取消
          </Button>
        )}
      </Form.Item>
    </Form>
  );
}
