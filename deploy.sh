#!/bin/bash

# 文档解析系统 Docker 部署脚本
# 用法: ./deploy.sh [--no-cache]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 项目配置
PROJECT_NAME="wanz-prase2-001"
COMPOSE_FILE="docker-compose.yaml"

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 Docker 是否运行
check_docker() {
    if ! docker info > /dev/null 2>&1; then
        log_error "Docker 未运行，请先启动 Docker"
        exit 1
    fi
    log_info "Docker 运行正常"
}

# 检查 docker-compose 文件是否存在
check_compose_file() {
    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "找不到 $COMPOSE_FILE 文件"
        exit 1
    fi
}

# 检查配置文件是否存在
check_config() {
    if [ ! -f "configs/config.yaml" ]; then
        log_error "找不到 configs/config.yaml 配置文件"
        exit 1
    fi
    log_info "配置文件检查通过"
}

# 停止并删除现有容器
stop_existing_container() {
    log_info "检查现有容器..."

    if docker ps -a --format '{{.Names}}' | grep -q "^${PROJECT_NAME}$"; then
        log_warn "发现已存在的容器: ${PROJECT_NAME}"
        log_info "正在停止容器..."
        docker-compose -f "$COMPOSE_FILE" down --remove-orphans
        log_info "容器已停止并移除"
    else
        log_info "未发现现有容器"
    fi
}

# 删除历史镜像
cleanup_old_images() {
    log_info "清理历史镜像..."

    # 获取当前镜像 ID（如果存在）
    OLD_IMAGE_ID=$(docker images -q "${PROJECT_NAME}:latest" 2>/dev/null)

    if [ -n "$OLD_IMAGE_ID" ]; then
        log_info "删除旧镜像: ${OLD_IMAGE_ID}"
        docker rmi -f "$OLD_IMAGE_ID" 2>/dev/null || true
    fi

    # 清理悬空镜像
    DANGLING=$(docker images -f "dangling=true" -q 2>/dev/null)
    if [ -n "$DANGLING" ]; then
        log_info "清理悬空镜像..."
        docker rmi $DANGLING 2>/dev/null || true
    fi

    log_info "镜像清理完成"
}

# 构建镜像
build_image() {
    local no_cache=$1

    log_info "开始构建镜像..."

    if [ "$no_cache" = "true" ]; then
        log_info "使用 --no-cache 模式构建"
        docker-compose -f "$COMPOSE_FILE" build --no-cache
    else
        docker-compose -f "$COMPOSE_FILE" build
    fi

    log_info "镜像构建完成"
}

# 启动容器
start_container() {
    log_info "启动容器..."
    docker-compose -f "$COMPOSE_FILE" up -d

    # 等待容器启动
    sleep 3

    # 检查容器状态
    if docker ps --format '{{.Names}}' | grep -q "^${PROJECT_NAME}$"; then
        log_info "容器启动成功!"
        log_info "服务地址: http://localhost:5019"
        log_info "API 文档: http://localhost:5019/docs"
    else
        log_error "容器启动失败，请检查日志: docker logs ${PROJECT_NAME}"
        exit 1
    fi
}

# 显示容器状态
show_status() {
    echo ""
    log_info "容器状态:"
    docker-compose -f "$COMPOSE_FILE" ps
}

# 主函数
main() {
    local no_cache="false"

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --no-cache)
                no_cache="true"
                shift
                ;;
            -h|--help)
                echo "用法: $0 [选项]"
                echo ""
                echo "选项:"
                echo "  --no-cache    重新构建镜像（不使用缓存）并清理历史镜像"
                echo "  -h, --help    显示帮助信息"
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                echo "使用 -h 或 --help 查看帮助"
                exit 1
                ;;
        esac
    done

    echo "=========================================="
    echo "  文档解析系统 Docker 部署"
    echo "=========================================="
    echo ""

    # 执行部署流程
    check_docker
    check_compose_file
    check_config
    stop_existing_container

    # 如果使用 --no-cache，先清理历史镜像
    if [ "$no_cache" = "true" ]; then
        cleanup_old_images
    fi

    build_image "$no_cache"
    start_container
    show_status

    echo ""
    log_info "部署完成!"
}

# 执行主函数
main "$@"
