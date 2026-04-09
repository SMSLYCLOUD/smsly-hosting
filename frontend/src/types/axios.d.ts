import 'axios';

declare module 'axios' {
  export interface AxiosRequestConfig<D = any> {
    _skipRemoteProxy?: boolean;
  }
}

