FROM buildpack-deps:jammy

ARG AIDER_MODAL_RUNTIME=0

# Install language/runtime dependencies. Modal supplies Python via add_python.
RUN apt-get update && apt-get install -y \
    software-properties-common \
    cmake \
    libboost-all-dev \
    ca-certificates-java \
    openjdk-21-jdk \
    libtbb-dev \
    && if [ "$AIDER_MODAL_RUNTIME" != "1" ]; then \
        add-apt-repository ppa:deadsnakes/ppa \
        && apt-get update \
        && apt-get install -y \
            python3.11 \
            python3.11-venv \
            python3.11-dev \
            python3-pip; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default python3 when Dockerfile owns Python.
RUN if [ "$AIDER_MODAL_RUNTIME" != "1" ]; then \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1; \
    fi

# Install Go with architecture detection
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then \
        GOARCH="amd64"; \
    elif [ "$ARCH" = "aarch64" ]; then \
        GOARCH="arm64"; \
    else \
        false; \
    fi && \
    curl -L "https://golang.org/dl/go1.21.5.linux-$GOARCH.tar.gz" -o go.tar.gz && \
    tar -C /usr/local -xzf go.tar.gz && \
    rm go.tar.gz
ENV PATH="/usr/local/go/bin:${PATH}"

# Install Rust
ENV RUSTUP_MAX_RETRIES=10
RUN curl -fsSL https://sh.rustup.rs -o /tmp/rustup.sh && \
    chmod +x /tmp/rustup.sh && \
    /tmp/rustup.sh -y --default-toolchain 1.96.0 && \
    rm /tmp/rustup.sh
ENV PATH="/root/.cargo/bin:${PATH}"

# Install Node.js and dependencies
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /npm-install && \
    cd /npm-install && \
    npm init -y && \
    npm install \
    jest@29.7.0 \
    @babel/core@7.25.2 \
    @exercism/babel-preset-javascript@0.2.1 \
    @exercism/eslint-config-javascript@0.6.0 \
    @types/jest@29.5.12 \
    @types/node@20.12.12 \
    babel-jest@29.6.4 \
    core-js@3.37.1 \
    eslint@8.49.0

COPY . /aider
RUN if [ "$AIDER_MODAL_RUNTIME" != "1" ]; then \
    pip3 install --no-cache-dir --upgrade pip uv; \
    fi
RUN if [ "$AIDER_MODAL_RUNTIME" != "1" ]; then \
    uv pip install --system --no-cache-dir -e /aider[dev]; \
    fi
RUN git config --global --add safe.directory /aider
WORKDIR /aider
