/**
 * Portfolio Selector Component
 * Dropdown to switch portfolios, with add/delete functionality
 */
import { useState, useEffect } from 'react';
import { Select, Button, Input, Space, Popconfirm, message } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { portfolioService } from '../services/portfolioService';
import { useAppContext, ActionTypes } from '../context/AppContext';

export default function PortfolioSelector() {
  const { state, dispatch } = useAppContext();
  const [newName, setNewName] = useState('');
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    loadPortfolios();
  }, []);

  const loadPortfolios = async () => {
    try {
      const portfolios = await portfolioService.getAll();
      dispatch({ type: ActionTypes.SET_PORTFOLIOS, payload: portfolios });
      if (portfolios.length > 0 && !state.activePortfolioId) {
        dispatch({ type: ActionTypes.SET_ACTIVE_PORTFOLIO, payload: portfolios[0].id });
      }
    } catch (error) {
      console.error('Failed to load portfolios:', error);
    }
  };

  const handleAdd = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const portfolio = await portfolioService.create(name);
      dispatch({ type: ActionTypes.ADD_PORTFOLIO, payload: portfolio });
      if (!state.activePortfolioId) {
        dispatch({ type: ActionTypes.SET_ACTIVE_PORTFOLIO, payload: portfolio.id });
      }
      setNewName('');
      setAdding(false);
      message.success('组合创建成功');
    } catch (error) {
      message.error('创建失败');
    }
  };

  const handleDelete = async () => {
    if (!state.activePortfolioId) return;
    try {
      await portfolioService.delete(state.activePortfolioId);
      dispatch({ type: ActionTypes.DELETE_PORTFOLIO, payload: state.activePortfolioId });
      message.success('组合已删除');
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleChange = (id) => {
    dispatch({ type: ActionTypes.SET_ACTIVE_PORTFOLIO, payload: id });
  };

  return (
    <Space>
      <Select
        value={state.activePortfolioId}
        onChange={handleChange}
        style={{ minWidth: 160 }}
        placeholder="选择组合"
        options={state.portfolios.map((p) => ({ label: p.name, value: p.id }))}
      />
      {adding ? (
        <Space.Compact>
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onPressEnter={handleAdd}
            placeholder="组合名称"
            style={{ width: 120 }}
            autoFocus
          />
          <Button type="primary" onClick={handleAdd}>确定</Button>
          <Button onClick={() => { setAdding(false); setNewName(''); }}>取消</Button>
        </Space.Compact>
      ) : (
        <Button icon={<PlusOutlined />} onClick={() => setAdding(true)}>新建组合</Button>
      )}
      {state.activePortfolioId && (
        <Popconfirm
          title="确定删除该组合？"
          description="组合内的所有持仓也会被删除"
          onConfirm={handleDelete}
          okText="确定"
          cancelText="取消"
        >
          <Button danger icon={<DeleteOutlined />} />
        </Popconfirm>
      )}
    </Space>
  );
}
