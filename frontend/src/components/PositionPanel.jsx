/**
 * Position Panel Component
 * Container for position form and list, scoped to active portfolio
 */
import { useState, useEffect } from 'react';
import { Card, Modal, Button, Empty } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import PositionForm from './PositionForm';
import PositionList from './PositionList';
import { positionService } from '../services/positionService';
import { useAppContext, ActionTypes } from '../context/AppContext';

export default function PositionPanel() {
  const [isModalVisible, setIsModalVisible] = useState(false);
  const [editingPosition, setEditingPosition] = useState(null);
  const [loading, setLoading] = useState(false);
  const { state, dispatch } = useAppContext();

  useEffect(() => {
    if (state.activePortfolioId) {
      loadPositions();
    }
  }, [state.activePortfolioId]);

  const loadPositions = async () => {
    setLoading(true);
    try {
      const positions = await positionService.getAll(null, state.activePortfolioId);
      dispatch({ type: ActionTypes.SET_POSITIONS, payload: positions });
    } catch (error) {
      console.error('Failed to load positions:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = () => {
    setEditingPosition(null);
    setIsModalVisible(true);
  };

  const handleEdit = (position) => {
    setEditingPosition(position);
    setIsModalVisible(true);
  };

  const handleModalClose = () => {
    setIsModalVisible(false);
    setEditingPosition(null);
  };

  const handleSuccess = () => {
    handleModalClose();
  };

  if (!state.activePortfolioId) {
    return (
      <Card title="持仓管理">
        <Empty description="请先创建或选择一个组合" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }

  return (
    <Card
      title="持仓管理"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          添加持仓
        </Button>
      }
      loading={loading}
    >
      {state.positions.length === 0 && !loading ? (
        <Empty description="暂无持仓数据" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <Button type="primary" onClick={handleAdd}>添加第一个持仓</Button>
        </Empty>
      ) : (
        <PositionList onEdit={handleEdit} />
      )}

      <Modal
        title={editingPosition ? '编辑持仓' : '添加持仓'}
        open={isModalVisible}
        onCancel={handleModalClose}
        footer={null}
        width={500}
      >
        <PositionForm
          editingPosition={editingPosition}
          onSuccess={handleSuccess}
          onCancel={handleModalClose}
        />
      </Modal>
    </Card>
  );
}
