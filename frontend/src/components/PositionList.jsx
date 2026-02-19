/**
 * Position List Component
 * Display and manage list of option positions with batch operations and filtering
 */
import { useState, useMemo } from 'react';
import { Table, Button, Popconfirm, Space, Tag, message, DatePicker, InputNumber, Row, Col, Select } from 'antd';
import { EditOutlined, DeleteOutlined, FilterOutlined, ClearOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { positionService } from '../services/positionService';
import { useAppContext, ActionTypes } from '../context/AppContext';

export default function PositionList({ onEdit }) {
  const { state, dispatch } = useAppContext();
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [filterSymbol, setFilterSymbol] = useState(null);
  const [filterExpDate, setFilterExpDate] = useState(null);
  const [filterMinStrike, setFilterMinStrike] = useState(null);
  const [filterMaxStrike, setFilterMaxStrike] = useState(null);

  const handleDelete = async (id) => {
    setLoading(true);
    try {
      await positionService.delete(id);
      dispatch({ type: ActionTypes.DELETE_POSITION, payload: id });
      setSelectedRowKeys((keys) => keys.filter((k) => k !== id));
      message.success('持仓删除成功');
    } catch (error) {
      message.error('删除失败');
      console.error('Delete error:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return;
    setLoading(true);
    try {
      await positionService.batchDelete(selectedRowKeys);
      dispatch({ type: ActionTypes.BATCH_DELETE_POSITIONS, payload: selectedRowKeys });
      message.success(`成功删除 ${selectedRowKeys.length} 条持仓`);
      setSelectedRowKeys([]);
    } catch (error) {
      message.error('批量删除失败');
      console.error('Batch delete error:', error);
    } finally {
      setLoading(false);
    }
  };

  const symbolOptions = useMemo(() => {
    const symbols = [...new Set(state.positions.map((p) => p.underlying_symbol))];
    return symbols.sort();
  }, [state.positions]);

  const filteredPositions = useMemo(() => {
    let data = state.positions;
    if (filterSymbol) {
      data = data.filter((p) => p.underlying_symbol === filterSymbol);
    }
    if (filterExpDate) {
      const target = filterExpDate.format('YYYY-MM-DD');
      data = data.filter((p) => p.expiration_date === target);
    }
    if (filterMinStrike != null) {
      data = data.filter((p) => p.strike_price >= filterMinStrike);
    }
    if (filterMaxStrike != null) {
      data = data.filter((p) => p.strike_price <= filterMaxStrike);
    }
    return data;
  }, [state.positions, filterSymbol, filterExpDate, filterMinStrike, filterMaxStrike]);

  const clearFilters = () => {
    setFilterSymbol(null);
    setFilterExpDate(null);
    setFilterMinStrike(null);
    setFilterMaxStrike(null);
  };

  const hasFilters = filterSymbol || filterExpDate || filterMinStrike != null || filterMaxStrike != null;

  const columns = [
    {
      title: '标的代码',
      dataIndex: 'underlying_symbol',
      key: 'underlying_symbol',
      fixed: 'left',
      width: 100,
    },
    {
      title: '类型',
      dataIndex: 'option_type',
      key: 'option_type',
      width: 80,
      render: (type) => (
        <Tag color={type === 'PUT' ? 'red' : 'green'}>{type}</Tag>
      ),
    },
    {
      title: '行权价',
      dataIndex: 'strike_price',
      key: 'strike_price',
      width: 100,
      render: (price) => `${price.toFixed(2)}`,
      sorter: (a, b) => a.strike_price - b.strike_price,
    },
    {
      title: '到期日',
      dataIndex: 'expiration_date',
      key: 'expiration_date',
      width: 120,
      render: (date) => dayjs(date).format('YYYY-MM-DD'),
      sorter: (a, b) => dayjs(a.expiration_date).unix() - dayjs(b.expiration_date).unix(),
    },
    {
      title: '数量',
      dataIndex: 'quantity',
      key: 'quantity',
      width: 80,
      render: (qty) => (
        <span style={{ color: qty > 0 ? 'green' : 'red' }}>
          {qty > 0 ? `+${qty}` : qty}
        </span>
      ),
    },
    {
      title: '开仓价格',
      dataIndex: 'entry_price',
      key: 'entry_price',
      width: 100,
      render: (price) => `${price.toFixed(4)}`,
    },
    {
      title: '操作',
      key: 'action',
      fixed: 'right',
      width: 120,
      render: (_, record) => (
        <Space size="small">
          <Button type="link" icon={<EditOutlined />} onClick={() => onEdit(record)} size="small">
            编辑
          </Button>
          <Popconfirm
            title="确定删除这个持仓吗？"
            onConfirm={() => handleDelete(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button type="link" danger icon={<DeleteOutlined />} size="small">
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const rowSelection = {
    selectedRowKeys,
    onChange: (keys) => setSelectedRowKeys(keys),
  };

  return (
    <div>
      <Row gutter={[12, 12]} style={{ marginBottom: 12 }} align="middle">
        <Col>
          <Space size="small">
            <FilterOutlined style={{ color: '#888' }} />
            <span style={{ color: '#888', fontSize: 13 }}>筛选:</span>
          </Space>
        </Col>
        <Col>
          <Select
            value={filterSymbol}
            onChange={setFilterSymbol}
            placeholder="标的代码"
            size="small"
            style={{ width: 120 }}
            allowClear
            options={symbolOptions.map((s) => ({ label: s, value: s }))}
          />
        </Col>
        <Col>
          <DatePicker
            value={filterExpDate}
            onChange={setFilterExpDate}
            placeholder="到期日"
            size="small"
            style={{ width: 130 }}
            allowClear
          />
        </Col>
        <Col>
          <InputNumber
            value={filterMinStrike}
            onChange={setFilterMinStrike}
            placeholder="最低行权价"
            size="small"
            style={{ width: 120 }}
            min={0}
          />
        </Col>
        <Col>
          <InputNumber
            value={filterMaxStrike}
            onChange={setFilterMaxStrike}
            placeholder="最高行权价"
            size="small"
            style={{ width: 120 }}
            min={0}
          />
        </Col>
        {hasFilters && (
          <Col>
            <Button type="link" size="small" icon={<ClearOutlined />} onClick={clearFilters}>
              清除筛选
            </Button>
          </Col>
        )}
        <Col flex="auto" style={{ textAlign: 'right' }}>
          {selectedRowKeys.length > 0 && (
            <Popconfirm
              title={`确定删除选中的 ${selectedRowKeys.length} 条持仓吗？`}
              onConfirm={handleBatchDelete}
              okText="确定"
              cancelText="取消"
            >
              <Button type="primary" danger size="small" icon={<DeleteOutlined />}>
                批量删除 ({selectedRowKeys.length})
              </Button>
            </Popconfirm>
          )}
        </Col>
      </Row>

      <Table
        rowSelection={rowSelection}
        columns={columns}
        dataSource={filteredPositions}
        rowKey="id"
        loading={loading}
        pagination={{
          pageSize: 10,
          showSizeChanger: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        scroll={{ x: 800 }}
        locale={{ emptyText: '暂无持仓数据' }}
      />
    </div>
  );
}
